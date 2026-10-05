# Complete Review Accounting And Compatible Evidence

Updated: 2026-10-05

## Changes

- Canonical review queues no longer inherit the 40-row display limit. Decision templates no longer
  inherit the 80-cluster preview limit. Preview limits of zero cannot make the queue empty.
- Queue provenance includes both producers; old truncated snapshots are stale until reconciliation.
- Compatible v3 word/turn labels survive audit metadata refresh, while changed words, nested word
  times, new unknown input fields, audio, models or roster invalidate reuse. Current quality is kept.
- The disclaimer identifies verified compatible evidence without promoting the strict publication
  or inventing additional labels. No frozen ASR/selector implementation or policy hash was changed.
- Candidate micro-cache validates its config/key, rechecks inputs after lock acquisition, rejects
  CPU-fallback reuse under a GPU request and publishes the alias JSON after its sidecars.

## Session Replay

An 85-minute local session was reconciled with `--skip-review-rebase --cached-speakers-only`.
This preserved raw CAF, ASR outputs, all source dialogues/text, review decisions and lifecycle reports.
Ordinary status/outcome/transcript reads made no writes. Display text is conserved and sorted, with
the same five anonymous labels and 96.0157% coverage. Coverage is not speaker accuracy.

The complete queue now contains 226 unresolved rows: 898.90s summed question intervals and 632.42s
unique audio. Previously the visible queue stopped at 40. The private replay report is under
`sessions/_reports/2026-10-05-review-publication/`; no recording or new ASR was needed for this check.

## Micro-Cache Evidence

Read-only inventory found 735 prepared micro clips, 324 unique PCM identities and 207 duplicate
groups. All 207 have identical saved transcription JSON, including relative word/segment times.
The 411 extra clips are a reuse opportunity, not a measured count of avoidable model invocations.
There are no identical-PCM groups spanning current and shadow in this session; the repeats are
within shadow windows/sources. Provenance of those consumers must stay separate even when PCM agrees.

Two real-model canaries passed with the unchanged configured `opportunistic`, nice 20, three-thread
policy: cold 3.009/2.023s, warm 0.014/0.006s, exactly one decode per pair and identical relative
transcription results. All inventoried inputs remained unchanged. These tiny samples measure cache
mechanics only; they do not predict full-session speedup or qualify a new production producer.

Reproduce the private inventory, optionally decoding two bounded cold/warm canaries:

```bash
.venv/bin/python scripts/check-micro-asr-cache-replay.py "$SESSION" \
  --out sessions/_reports/micro-cache-check/report.json \
  --decode-groups 2 --timeout-sec 120
```

Omit `--decode-groups` for inventory only. The checker reads the configured resource policy, changes
no session inputs, writes separate artifacts, enforces a deadline during lock/decode and records
actual decode counts. Its report always leaves production `DO_NOT_PROMOTE`: a cache canary is not
a full producer/conservation qualification. The old corpus lacks raw in 11 of 12 sessions; replacement
corpus qualification and integration remain in Bounded Evidence Compute v1.

## Checks

- The earlier capture-continuity change is finalized separately: continuous media timestamps do
  not acquire false holes from delayed callbacks; genuine timestamp jumps still insert silence.
  Initial alignment and absent/invalid timestamps retain explicit wall-clock provenance, and
  historical manifest rows remain readable. Boundary tests cover the 50ms tolerance and clock reset.
- `check-review-queue-snapshot.py`: 105 disjoint rows cross both former limits; closing the first 40
  leaves 65 rows and 130 seconds. Zero/one/normal/large preview limits preserve the template.
- `check-review-publication.py`: compatible metadata preserves labels/current quality without
  inference; acoustic/nested input changes reject reuse; interrupted publication preserves pointers.
- `check-micro-asr-cache.py`: concurrent callers, invalidation, corrupt config, lock timeout,
  changed inputs during wait, CPU fallback, relative-time rebinding and replay inventory.
- Focused checks, `swift build --jobs 3`, `git diff --check` and the full `scripts/check.sh` passed.
  The earlier run includes 14 Swift tests, Python regression checks, lifecycle/resume/sidecar smoke tests,
  planning consistency, source and release-bundle acceptance, and the final CLI fixture.
  An initial suite run stopped during documentation edits (a not-yet-created report link); the
  completed tree was rerun from the start. Documentation length constraints were corrected too.
- Finalization adds three capture boundary regressions. The final full `scripts/check.sh` rerun
  passed on 2026-10-05 with 17 Swift tests, all Python checks, source/release-bundle acceptance and
  the final CLI smoke fixture. The local log is `/tmp/murmurmark-finalize-20261005-check.log`.
- Two real-model cold/warm canaries passed as measured above. No fresh microphone capture or
  full-session cold ASR rerun was performed. Production cache integration remains unqualified.
