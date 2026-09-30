# Review-Safe Attributed Handoff: 2026-09-30

Scope: stages 1-4 (R1-R4/R6) of the
[repair plan](../project/2026-09-30-transcript-reliability-repair-plan.md).
R5 is a tested candidate, not a production speedup. R7/R8 remain separate work.

## Changes

- Remote-only projection preserves compatible v1/v3 provisional assignments across Me deletions.
  Remote review `needs_review: true -> false` retains frozen, nonexpanding eligibility; reverse
  changes and other dependent input changes invalidate reuse. Strict selector remains byte-exact.
- Text-only keep is not proof of local voice. Automatic keep needs current bounded audio evidence;
  conflicting remote-explained mic, lexical/order/local-recall tasks and nested integrity risks stay
  unresolved. Decision source, not profile name, owns human/automatic/legacy provenance.
  Boolean, nonnumeric, nonfinite and out-of-range confidence values cannot authorize keep or drop.
- Interactive review records human provenance only for new explicit answers; opening an old journal
  does not upgrade its legacy entries. An incomplete suggested lane routes to the separate manual
  answer sheet, never to editing generated evidence-bound suggestions.
- Pipeline/CLI distinguish cooperative 75, user 130 and real failure. Waiting for the compute lease
  obeys the same deadline. Journal checkpoints retain completed work and raw child diagnostics.
- A new adversarial test reproduced an orphan nested worker after deadline. The shared supervisor
  now tracks owned descendants across process groups using PID/birth-time identity and escalates
  only those workers. The same test passes after the fix.
- Progress produces a fingerprint-bound queue snapshot with unresolved tasks, interval sum,
  any-track timeline union and unknown durations. Reconciliation verifies exact agreement;
  readers must not present stale counts as current. Historical run reports are not rewritten.

## Local Corpus

Private paths and hashes are stored under
`sessions/_reports/transcript-reliability-repair-2026-09-30/`; public fixtures are synthetic.
No meeting content or human identity is included here.

| Case | Purpose | Provisional remote coverage | First cached refresh |
| --- | --- | ---: | ---: |
| A | Group, review and remote-neighbor conflict | 84.9690% | 12.216s |
| B | Me deletion plus remote quality change | 91.6725% | 8.338s |
| C | Independent earlier group with two clusters | 49.5416% | 9.956s |

All three cached reconciliations passed without new primary ASR. Raw CAF, selected/base dialogue,
text/quality and historical lifecycle/pipeline reports were unchanged. B previously lost all
provisional labels after review; it now retains compatible frozen labels. Coverage is neither
speaker accuracy nor lexical accuracy. Unknown speech and export/quality blockers remain explicit.
This measures report/publication repair latency, not whole-meeting processing time.
The final replay also ran `status`, `outcome` and `transcript --path-only` twice per session:
each returned the current publication without changing any session file's size or modification
time. Reconciliation times were 11.082s / 7.886s / 8.558s for A/B/C in that replay.

## Cache Boundary

`micro_asr_cache.py` tests exact PCM/config identity, concurrent callers, cache corruption, force,
prompt/model/executable/environment changes, relative timestamps, cancellable locks and atomic
generation replacement. Current/shadow consumers may share a session-local cache; unrelated
sessions must not. It is not connected to the frozen primary transcriber.

The production transcriber and strict speaker selector are pinned by qualified policies. Changing
their implementation hashes without qualification would disable promoted behavior. Their source
and policy identities are unchanged by this repair. Raw is missing for 11/12 historical Echo v2.17
qualification sessions. Bounded Evidence Compute v1 must qualify a newly declared reproducible
corpus with all existing conservation/fallback gates and measure fixed-input cold/warm decodes.
No production latency percentage or full R5 completion is claimed.

## Verification

Targeted publication, queue, review evidence, materialization, processing lease, cache and
reconciliation checks are part of `scripts/check.sh`. Publication checks include actual subprocess
75/130/2 propagation and the nested-worker timeout case. A three-session private replay is available:

```bash
.venv/bin/python scripts/check-review-publication.py \
  --real-session "$SESSION_A" --real-session "$SESSION_B" --real-session "$SESSION_C" \
  --report sessions/_reports/transcript-reliability-repair-2026-09-30/publication-corpus.json
```

The staged release snapshot, excluding the earlier capture timeline experiment, passed debug and
release builds, all nine included Swift tests, release CLI self-test and focused publication,
review evidence, queue, reconciliation, lifecycle, unchanged capture-continuity and release-layout
checks. After tightening confidence validation, all six affected review/quality checks and the
three-session replay were repeated successfully. The final smoke fixture covers the manual review
handoff and passes; its long help checks avoid the `echo | grep -q` / `pipefail` race.
Full workspace `scripts/check.sh`: passed, including both automated acceptance runs (workspace
and packaged release), the release-integrity checks and the final smoke fixture. The exact staged
Swift source also passed an uncached `swiftlint lint Sources Tests` run in the isolated checkout.
An isolated full-suite attempt stopped when SwiftLint scanned generated `.build` runner code under
the temporary checkout. The normal workspace run uses its configured exclusions; no lint rules
were weakened. Isolated builds and focused source/behavior tests are recorded separately. After
the full run, only documentation and the planning check's expected frontier were updated; the
planning check, official OpsKarta validation and diff checks were repeated.
No new capture, full-meeting ASR rerun or human accuracy annotation was performed. Earlier dirty
storage preflight, resource caps/FIFO lease and overlap reconstruction fixes are included and
covered by regression checks. The separate capture timeline experiment and its fixtures remain
outside the staged release; no live soak is claimed for that experiment.
