# Review Evidence And Shared Follow-Up Budget

The 2026-09-24 session audit exposed three gaps: a heuristic noise suggestion could be applied with
an empty evidence receipt, micro-ASR stability flags disappeared between the audio pack and readiness,
and unbounded report/review actions could overrun a correctly bounded enrichment stage. Quick stronger
judge decodes also lacked the word timestamps needed to localize speech in padded clips.

## Changes

- Require sealed, matching audio evidence both when generating and applying automatic Me drops.
  Empty evidence, missing required sources, ambiguous scope and contradictory local voice remain review.
- Carry stability flags through the audio audit and readiness. Keep manual answer handling unchanged.
- Request word timestamps in production and targeted stronger-judge calls, with separate decode identities.
- Share a monotonic deadline across enrichment, report, preview, apply, final refresh and export.
  Defer remaining optional work, retain the transcript and expose an explicit bounded resume command.
  Resume refreshes suggestions after a partial apply without rerunning completed capture or ASR.

## Regression Coverage

- `check-review-audio-evidence.py`: empty receipts, missing sources/scope, conflicting voice, flag propagation,
  positive sealed duplicate, stale evidence rejection in both apply routes, bounded words and cache separation.
- `check-review-materialization-guard.py`: unsealed noise cannot delete; sealed four-source evidence can.
- `check-authoritative-handoff.py`: production stronger judge requests word timestamps.
- `check-meeting-lifecycle.py`: timeout each optional follow-up, shared cumulative budget, no surviving
  timed-out worker, raw/transcript preservation, intact enrichment checkpoint, explicit bounded resume.
  A real Swift CLI probe covers a Python helper in another process group and its detached child that
  ignores SIGINT; timeout escalation must stop both. Recycled PIDs are not treated as descendants.
- `smoke-fixture.sh`: text-only and absent-judge fallback must no longer generate automatic drops.

These changes do not reprocess real sessions or revise historical review decisions. They do not alter
capture, Echo Guard, main ASR, model selection or the configured CPU/resource profile.

## Verification Result

On 2026-09-24, the complete `scripts/check.sh` run passed: Swift build, 13 Swift tests, lint,
Python compilation and regression checks, corpus contracts, CLI/release acceptance and the final
smoke fixture. Focused lifecycle tests were rerun after the final timeout-cleanup changes, including
user interruption during cleanup of detached workers. `git diff --check` passed.

Read-only replay of `arp_000015` from `2026-09-24_11-15-03` now returns `needs_review` without audio
evidence, instead of the historical automatic `drop_me`. No saved decision or transcript was changed.
No new live capture or full real-session ASR/stronger-judge rerun was performed, so these checks do not
establish a new end-to-end speed or recognition-accuracy measurement for a real meeting.
