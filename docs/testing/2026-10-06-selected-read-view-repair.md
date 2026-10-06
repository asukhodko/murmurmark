# Selected Read View Verification

Updated: 2026-10-06. Private speech, session IDs and detailed evidence remain in ignored reports.

## Corpus Replay

Four available sessions were frozen before publication changes: raw CAF, selected dialogue,
saved ASR JSON and original publication hashes. Three already had earlier immutable provisional
baselines. One strict pointer in the new snapshot was already stale; the earlier pre-repair
provisional baseline is the correct selected-publication reference. Both original baselines remain
unchanged and the replay records their SHA-256 values. No post-change output was substituted for
a pre-change expectation.

| Case | Speaker state | Open questions | Unplaced historical-profile questions | Conservation |
| --- | --- | ---: | ---: | --- |
| A | selected | 107 | 0 | pass |
| B | provisional | 156 | 0 | pass |
| C | provisional | 229 | 0 | pass |
| D | provisional | 80 | 12 | pass |

All four replays passed source text/roles/times/labels, available coverage summary, raw/ASR hashes,
previous immutable publications, current full queue, JSON/Markdown agreement and outcome path
checks. Default debug CLI reads returned the new reading projection for all four. Quality/export
gates and speaker eligibility were not promoted. Closed answers were not changed or reopened;
an existing `needs_review` answer remains an open question.

A has 467 display turns: 201 ordinary utterance times, 151 valid child times and 115 approximate
parent-range turns. The latter include invalid nested boundaries; no clamping or lexical retiming
was performed. A's 95 text questions are represented inside the complete 107-question set.
Metadata consistency is not acoustic accuracy, and the clips were not independently hand-graded.

## Targeted Checks

- Strict, provisional, unavailable and aggregate-fallback source projections.
- Full pending template plus retained historical decisions; closed questions are not resurrected.
- Different-profile IDs cannot attach questions to current utterances; missing queue is unknown.
- Exact text conservation, speaker-label preservation, valid child ordering, invalid parent fallback,
  cross-parent intersections and unknown times.
- Relative/absolute session-path equivalence; stale source/context/implementation and corrupt outputs
  are rejected. External paths are rejected.
- CLI pins the expected generation before verification; a concurrent pointer replacement cannot
  substitute a different, unverified generation between validation and opening the file.
- Deadline/interruption before pointer publication leaves the previous selection intact.
- Run-scoped activity observations ignore unchanged old files, distinguish current/shadow/primary,
  preserve elapsed wall-time accounting and leave unavailable decode/cache-hit counters null.
- Observer permission failure is nonfatal; pipeline integration emits diagnostic metadata without
  changing the child result.
- SHA-256 publication checks work on system Python 3.9 without `hashlib.file_digest`;
  empty, small and multi-block inputs have the same digest.

Synthetic checks: `check-transcript-read-view.py`, `check-transcript-publication.py`,
`check-transcribe-observation.py`. Release validation uses the full `scripts/check.sh` suite.

## Compute Boundary

The latest read-only micro inventory has 623 clips, 296 unique PCM and 327 extra identical copies.
This does not establish historical model calls or the time saved by reuse. No full-session cold/warm
benchmark was run, no producer cache was enabled, and no new model inference was needed for this
publication replay. Existing producer/strict-selector policy hashes are unchanged.

The decision for cache integration, new word selection and cluster-gate changes remains
DO_NOT_PROMOTE pending replacement-corpus qualification and independent word/voice truth.
Eleven of twelve old Echo raw pairs are unavailable. Capture and resource policy were not changed.

## Reproduction

```bash
.venv/bin/python scripts/check-transcript-evidence-repair-corpus.py \
  --baseline sessions/_reports/selected-read-view-2026-10-06/baseline.json \
  --publication-baseline sessions/_reports/transcript-evidence-repair-2026-10-06/baseline.json \
  --out sessions/_reports/selected-read-view-2026-10-06/replay-final.json \
  --check-read-view
```

Reports cannot overwrite either baseline. `--publication-baseline` supplies only an earlier
publication reference; raw/source conservation still uses the original input baseline.
For future freezes the helper respects the saved outcome's provisional selection instead of
blindly trusting a stale strict `state=selected` pointer.

## Release Validation

`env OPENBLAS_NUM_THREADS=3 OMP_NUM_THREADS=3 MKL_NUM_THREADS=3 nice -n 20 scripts/check.sh`
completed with exit code zero. Swift build, 17 Swift tests, SwiftLint, Python compilation,
regression suites, release-quality contract, retention/lifecycle checks, public-content checks
and fixture smokes passed. Separate planning consistency and official OpsKarta validation passed.
The committed-PCM hardware capture and CLI/release acceptance branches were skipped because
ScreenCaptureKit reported no shareable display. No live recording was used as release evidence.

Initial checks caught and corrected relative-path identity, historical-queue scope and
documentation-size issues; no conservation or quality check was relaxed. Four real sessions also
passed the reconciliation consistency checks after the final reading projections were published.

After the full-suite run, generation-pinning and Python 3.9 compatibility received focused rechecks:
debug/release builds and SwiftLint for the CLI change; Python compilation, read-view/publication,
observer, review/deadline and release-quality checks; and the four-session conservation replay.
The installed release `murmurmark self-test` passed. Its obsolete provisional-path expectation now
checks the reading path plus the preserved source selection and verified projection instead.
The fixture handoff smoke is now unconditional in `check.sh`, independent of display availability.
