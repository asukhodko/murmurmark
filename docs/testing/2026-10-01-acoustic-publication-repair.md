# Acoustic Publication Repair: 2026-10-01

Scope: provisional publication and review safeguards from the
[repair checkpoint](../project/2026-10-01-acoustic-publication-repair.md).
The primary ASR producer, strict speaker publisher and qualified policies are unchanged.

## Local Corpus

Private baseline hashes and replay evidence are in
`sessions/_reports/acoustic-publication-repair-2026-10-01/`. Public fixtures use synthetic
text and audio. No meeting content or human identity is included here.

| Case | Purpose | Sound-bound turns | Parent-time fallback turns | Warning turns | Cached reconciliation |
| --- | --- | ---: | ---: | ---: | ---: |
| S | Short recording with remote digital silence before playback | 1 | 0 | 2 | 1.297s |
| M | Current long meeting with nested speaker turns and micro-ASR | 5 | 39 | 105 | 14.596s |
| G | Independent earlier group meeting | 4 | 74 | 168 | 14.965s |

These are display-turn counts, not independent utterances or measured accuracy. The timings
measure cached report/publication refresh, not transcription speed. No primary ASR ran.

All three replays passed: source words, roles, timestamps, quality, raw CAF, ASR artifacts and
historical lifecycle/pipeline reports were unchanged. Flattened JSON and Markdown agree, retain
all parent text and have ordered known display starts. Missing nested time falls back to an
explicitly approximate parent interval instead of zero. Read-only `status`, `outcome` and
`transcript --path-only` were each run twice and did not modify session files.

In S, remote text previously displayed before earlier local speech. The first nonzero remote
sample is at 20.98875 seconds; the read view now places that remote phrase no earlier than this
bound and marks the time approximate. This proves a silence bound, not the exact word onset.
An all-silent window retains the source time with a warning rather than moving or deleting text.

The separately frozen 68-file baseline had 65 unchanged files and only three intentional changes:
the provisional selection pointers. No source file disappeared. New text-review checks can
increase the honest review queue; fewer reported questions is not a release criterion.

## Automated Checks

Synthetic regressions cover interleaving local/remote turns, invalid nested time, missing time,
text conservation, nested review reasons, cleared history, broad-context micro-ASR, partial or
out-of-target word evidence, stale legacy stability, remote text-review safety, and independence
of voice/chronology/text questions. Acoustic cases include opposite-phase stereo, tiny nonzero
signals, exact silence, missing/corrupt/unreadable raw, multiple raw files and audio changing
during inspection. Audio/helper/layout changes invalidate stored timing evidence.

The isolated staged source, excluding pre-existing capture edits, passed `swift test -j 3`
(nine tests), debug/release builds, release CLI self-test, provisional publication, display
projection, review materialization, publication deadline and release-layout checks. Final helper
changes were repeated in this isolated source. Missing publication helpers are now rejected by
release validation rather than failing on import after installation.

The workspace `scripts/check.sh` run passed its build, fourteen Swift tests (including separate
uncommitted capture tests), lint/compile checks, regression checks, static capture checks, sidecar
smokes, and both workspace and packaged-release acceptance. Its final smoke fixture stopped on
an outdated assertion expecting one text-review question. The corrected assertion requires all
four independent text questions to remain unresolved, with no automatic keep/drop. The entire
`scripts/smoke-fixture.sh` then passed. A second whole-suite run was not repeated after this
test-only correction; publication and release changes had separate targeted reruns. Planning,
official OpsKarta validation, open-source readiness and diff checks also passed.

## Remaining Qualification

This is not an upstream timing, lexical accuracy or speaker-purity promotion. The strict publisher
is unchanged. Broad micro-ASR text is preserved and marked uncertain; the producer still needs
word-bounded selection. Remote alignment before overlap detection and Me repair remains open.
The exact-input micro-ASR cache remains a candidate without a production speed claim.

Eleven of twelve historical Echo qualification sessions lack raw. A newly declared reproducible
corpus is required before changing frozen producer fingerprints. No new live recording, full ASR
rerun, human accuracy annotation or resource-profile change was performed for this release.
