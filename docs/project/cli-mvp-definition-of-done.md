# CLI MVP Definition of Done

MurmurMark reaches CLI MVP when the project is usable as a local command-line
tool, not as a set of research scripts. The goal is not perfect transcription.
The goal is a repeatable local workflow that records, processes, reports risk,
exports reviewed artifacts and protects raw audio.

## Automated Gate

Run:

```bash
murmurmark acceptance
```

For a faster local check without release bundle verification:

```bash
murmurmark acceptance --skip-release
```

To keep a machine-readable evidence file for the gate:

```bash
murmurmark acceptance --skip-release --report /tmp/murmurmark-acceptance.json
```

The automated gate must pass these checks:

1. `scripts/install-local.sh` installs a working `murmurmark` wrapper into a temporary prefix.
2. `murmurmark doctor --strict` succeeds through the installed wrapper.
3. `murmurmark self-test` succeeds through the installed wrapper.
4. `murmurmark config init --config <temp>` creates a local config without writing to the repository.
5. `scripts/check-open-source-readiness.sh` succeeds.
6. `scripts/build-release-bundle.sh --verify` succeeds unless the gate is intentionally run with `--skip-release`.

When `murmurmark acceptance` runs from a release bundle instead of a developer checkout, it verifies
the bundle with `doctor --strict`, `self-test` and local config initialization. Open-source readiness
and release-bundle construction stay developer-checkout checks.

The direct `scripts/acceptance-cli-mvp.sh` entry point remains available when debugging the gate
implementation.

The gate must finish with:

```text
status: ok
```

When `--report PATH` is used, the report must use schema
`murmurmark.cli_mvp_acceptance_report/v1`, contain `status: ok`, list every automated check and keep
the live recording gate as `manual`.

## Manual Live Recording Gate

Print the checklist:

```bash
murmurmark acceptance --live-checklist
```

Run this on a machine with macOS recording permissions granted:

```bash
murmurmark doctor
murmurmark self-test
murmurmark config init
murmurmark record --target-bundle system
murmurmark inspect latest
murmurmark process latest
murmurmark status latest
murmurmark next latest
murmurmark acceptance --live-session latest --report /tmp/murmurmark-live-session.json
```

Then follow the printed review command when readiness says `review_first`.
When the session is exportable, run:

```bash
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
```

The manual gate passes when:

- the recording creates separate non-empty mic and remote tracks;
- `process latest` completes or prints a concrete next command;
- `status latest` reports a clear readiness state;
- `acceptance --live-session latest` reports `status: ok` and writes a report with
  `manual_gates.live_recording.status = passed`;
- risky transcript regions remain explicit review items;
- export is blocked while required review/export blockers exist;
- a successful export writes a manifest;
- retention planning does not delete raw audio without an explicit `apply` and confirmation flag.

## Non-Goals For This Gate

- perfect transcript quality;
- full diarization inside `Colleagues`;
- UI app or menu bar app;
- cloud transcription or cloud summarization;
- automatic raw audio deletion;
- automatic publishing to external systems.

## Release Readiness

Before public sharing, also resolve the owner decision listed in
[`open-source-readiness.md`](open-source-readiness.md): choose and publish the
security contact.
