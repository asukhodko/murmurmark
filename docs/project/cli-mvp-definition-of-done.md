# CLI MVP Definition of Done

Status: achieved.

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

The checklist has two sections:

- `live_recording_gate`: the production batch-first route for a real meeting;
- `near_realtime_shadow_gate`: lab proof and existing-session comparison only while real sidecar
  capture is quarantined, including the system-audio probe, live segment fail-open probe and a
  blocked `promotion_policy`.

Run this on a machine with macOS recording permissions granted:

```bash
murmurmark doctor
murmurmark self-test
murmurmark config init
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
murmurmark inspect "$SESSION"
murmurmark process "$SESSION"
murmurmark status "$SESSION"
murmurmark next "$SESSION"
murmurmark acceptance --live-session "$SESSION" --report /tmp/murmurmark-live-session.json
```

Then follow the printed review command when readiness says `review_first`.
When the session is exportable, run:

```bash
murmurmark finish "$SESSION"
```

Do not use `--live-pipeline` for this production gate. Before any near-realtime coverage attempt,
run:

```bash
MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live-evidence"
murmurmark record --out "$SESSION" --target-bundle system --duration 120 --experiment live-shadow-v1
murmurmark process "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
murmurmark corpus live all --refresh
jq '.promotion_policy' sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

`promotion_policy.status` must remain `blocked`, `batch_authoritative` must remain `true`,
`new_real_live_collection_allowed` must remain `false`, and `real_parity_dimensions.capture_safety`
must be present until capture safety plus live-vs-batch parity coverage are explicitly approved. The
pilot runner writes `derived/live/live_parity_pilot_report.json` under the pilot session and keeps
the selected batch transcript authoritative.

The manual gate passes when:

- the recording creates separate non-empty mic and remote tracks;
- the recording status is not `partial`, and `inspect "$SESSION"` shows an explicit stop reason such as
  `sigint` or `duration_elapsed`;
- `process "$SESSION"` completes or prints a concrete next command;
- `status "$SESSION"` reports a clear readiness state;
- `acceptance --live-session "$SESSION"` reports `status: ok` and writes a report with
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

## Current Evidence

The gate was last proven with these checks:

- `murmurmark acceptance --report /tmp/murmurmark-acceptance-full.json` finished with `status: ok`
  and verified the release bundle.
- `murmurmark acceptance --live-session sessions/2026-06-26_17-31-17 --report /tmp/murmurmark-live-session-final.json`
  passed for `sessions/2026-06-26_17-31-17`.
- `murmurmark finish sessions/2026-06-26_17-31-17` wrote a local export bundle plus retention and payload manifests.

Remaining publication work is outside this CLI MVP gate: decide the public security contact and
choose the repository history/push strategy.
