# Current Goal: Recording Reliability

Status, 2026-06-30: completed.

MurmurMark must make recording reliable enough for ordinary working meetings.
`murmurmark record` should keep capturing until the user explicitly stops it with
`Ctrl-C` or until a requested `--duration` ends. If capture stops for any other
reason, MurmurMark must not pretend that the meeting was recorded normally.

## Goal

Make `murmurmark record` safe to trust at the start of a meeting:

- a normal user stop produces a completed session and the next command is `murmurmark process`;
- an unexpected stop produces a durable partial session with a clear reason, timestamps, track health
  and a next command that starts with `murmurmark inspect`;
- normal `murmurmark process SESSION` refuses partial recordings unless `--allow-partial` is explicit;
- `murmurmark status`, `murmurmark next`, `murmurmark sessions` and `murmurmark inspect` all show the
  same partial-capture state;
- setup problems that would prevent `record --target-bundle system` from starting are visible in
  `murmurmark doctor` before a real meeting starts.

## Out Of Scope

- Changing the capture topology.
- Changing Echo Guard, ASR, transcript repair, cleanup or synthesis.
- Recovering missing audio after macOS or ScreenCaptureKit stopped delivering it.
- Treating a partial meeting as exportable by default.

## Done When

- A short duration recording exits successfully and writes `stop_reason: duration_elapsed`.
- A no-duration recording stopped by `Ctrl-C` exits successfully and writes `stop_reason: sigint`.
- A no-duration recording stopped by `SIGTERM`, `SIGHUP`, ScreenCaptureKit stop or capture stall writes
  `status: partial`, `health.partial: true` and `capture.stopped.partial: true`.
- `status` and `next` recommend `inspect`, not `process`, for partial recordings.
- The pipeline blocker explains that `--allow-partial` is debugging-only.
- The smoke fixture covers partial recording behavior.
- README, runbooks, contracts and roadmap describe the behavior.

## Verification

- `swift build`
- `.venv/bin/python -m py_compile scripts/run-session-pipeline.py scripts/report-operational-readiness.py`
- `git diff --check`
- `scripts/smoke-fixture.sh`
- `scripts/check.sh`
- Live duration smoke: `record --duration 2 --target-bundle system` exited successfully with
  `stop_reason: duration_elapsed`.
- Live signal smoke: no-duration `record` stopped by `SIGINT` exited successfully with
  `stop_reason: sigint`.
- Live partial smoke: no-duration `record` stopped by `SIGTERM` and `SIGHUP` wrote `status: partial`,
  `health.partial: true`, `explicit_stop: false`, and `status`/`next` recommended `inspect`.
- Live partial pipeline smoke: normal `process` refused the partial SIGHUP package and printed
  `--allow-partial` as debugging-only.
