# Cached Speaker Publication After Review

Historical keep-only repair. The [September 30 follow-up](2026-09-30-review-safe-handoff.md)
extends reuse to Me deletion and nonexpanding remote review eligibility, tightens review evidence,
and tests cancellation of nested process groups. The measurements below retain their original scope.

## Confirmed Defects

Two September 25 sessions exposed a publication failure after successful capture and primary ASR.
Keep-only review changed profile/quality metadata without changing words, roles, source intervals
or timestamps. Speaker refresh ran before readiness selected the reviewed profile; subsequent
refreshes recomputed evidence and could exhaust the optional budget. Ordinary Swift readers also
materialized evidence despite a cache-only Python finalization. A diagnostic-only change to a strict
fallback reason invalidated an otherwise unchanged provisional view.

Budget cancellation surfaced as `python exited with 2` or an unhandled `KeyboardInterrupt` even
when useful judge/apply checkpoints survived. The batch report could still describe an earlier
pass rather than the partially completed current pass.

## Implementation

- Publish readiness/review-plan changes before speaker materialization. Review convergence uses
  `--cached-only`; ordinary transcript/status reads use `--verify-only` and do not publish files.
- Rebind compatible v1 clusters or v3 word/turn evidence as provisional, with a source identity and
  projection SHA-256. All utterance IDs, text, roles, intervals, ordering and source references must
  match. Remote acoustic eligibility remains unchanged; only human/order review annotations are
  ignored. Local quality annotations are not remote inference inputs.
- Validate frozen manifests, raw/prepared remote audio, installed model, roster and implementation.
  V3 retains promoted v2/v3 lineage and raw word timestamps. Changed inputs reject reuse. Consensus
  model reuse is not supported in this version. No strict policy/gate is relaxed.
- Write provisional JSON and Markdown to immutable generation paths, then atomically publish the
  selection pointer. Compatibility copies are not the commit point. Diagnostic-only strict reason
  changes no longer invalidate the selection.
- Propagate an action deadline into children, stop new review passes when time is insufficient,
  checkpoint batch/reconciliation stages, and return 75 for deferred budget exhaustion or 130 for
  interruption. Cooperative child exits carry the same meaning as an external watchdog timeout.
  Genuine child failures remain failures. The lifecycle retains its hard-stop watchdog.

## Real Session Replay

Both sessions were reconciled using existing local artifacts, without primary ASR or new model
inference. Commands used `--skip-review-rebase --cached-speakers-only`. On the final measured pass:

| Session | Reconcile | Remote speech attributed | Anonymous clusters | Remaining review |
| --- | ---: | ---: | ---: | ---: |
| `2026-09-25_12-01-18` | 8.351s | 49.5416% | 2 | 40 rows / 112.92s |
| `2026-09-25_14-15-45` | 4.133s | 95.0042% | 1 | 6 rows / 9.16s |

Both read surfaces remain `provisional`. The afternoon result preserves v3 word-weighted coverage
and speaker turns rather than substituting the weaker utterance-level v1 result. Coverage is not
diarization accuracy or verified human identity. No new review decisions were applied; existing
18 keep decisions in the afternoon session remain present. Review/export gates were not cleared.

Fresh SHA-256 checks matched all four raw CAF files against the pre-repair audit. Selected dialogue,
aggregate Markdown and all primary-ASR JSON/cache files were byte-identical before/after replay.
Actual debug CLI `status`, `outcome` and `transcript --path-only` calls changed no session files
(size/mtime snapshots); outcome and transcript resolved to the same immutable generation. Historical
lifecycle reports retain the original run's timings and failures rather than being rewritten as a
successful new meeting.

Private diagnostic details are in `sessions/_reports/session-audit/2026-09-25_12-01-18.md` and
`sessions/_reports/session-audit/2026-09-25_14-15-45.md`. Those are pre-repair observations, not
current-state reports. Transcript content is deliberately not copied into this public note.

## Tests And Limits

`check-review-publication.py` covers no-inference compatible v1/v3 rebinding, within-utterance speaker
turns, deterministic replay, interrupted publication, read-only CLI access, remote/text/interval/
model/roster/audio invalidation, refresh ordering, deadline cancellation and preservation of partial
apply results. Cooperative exits 75/130 and a genuine failure exit 2 are distinguished. Existing
speaker naming/coverage checks now explicitly publish before testing ordinary readers. The actual
Swift report command is also tested for deferred/interrupted exits without a misleading Python
failure. The CLI self-test explicitly publishes its speaker view, verifies read-only status, and
supplies an empty overlap artifact for its single-utterance fixture.

Final verification on September 25 passed:

- `nice -n 20 scripts/check.sh`: Swift build and tests, SwiftLint, Python compilation, contract,
  regression, corpus and smoke checks. Automated acceptance/release-acceptance was skipped because
  ScreenCaptureKit reported no shareable display; the synthetic release-layout check passed.
- `MURMURMARK_BIN="$PWD/.build/debug/murmurmark" nice -n 20 scripts/smoke-cli-handoff.sh`:
  the separate CLI self-test passed despite the display-dependent acceptance skip.
- Focused publication, reconciliation, authoritative-handoff and lifecycle tests; final cached
  replay and read-only verification of both real sessions; shell syntax and `git diff --check`.

No new capture/soak, full meeting resume, primary-ASR rerun, manual listening or speaker-accuracy
measurement is part of this repair. Cached replay timings do not predict cold ASR/diarization speed.
The frozen primary transcriber, strict speaker selector, echo/capture paths and resource configuration
were not changed by this repair. The installed release executable still needs rebuilding to use
the Swift reader/deadline changes.
