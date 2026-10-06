# Transcript Evidence Repair Verification

Updated: 2026-10-06. Private session identifiers, speech, model outputs and paths stay below
ignored `sessions/_reports/transcript-evidence-repair-2026-10-06/`.

## Implemented Boundaries

- Ordinary Me/remote interval refinement checks are shared by provisional JSON/Markdown and the
  full text-review queue. The short-micro-only 1.25-second cutoff cannot hide those candidates.
- Original source, padded recognition slice and selected interval are distinct. Metadata remains
  diagnostic: words, roles, timestamps and speaker eligibility are not automatically repaired.
- Complete word support suppresses the new interval warning; missing/partial/malformed support
  does not. Short acknowledgements and ordinary silence trimming are negative controls.
- Automatic keep/drop cannot resolve an interval question, including through an old local-voice receipt.
- Final refresh uses cached evidence, skips review rebase and WAV construction, and keeps its
  30-second deadline. Metadata-only workspace output is separate from full packs and answer sheets;
  writes are atomic, incompatible flags are rejected, and the index cannot be applied as answers.
- Shared listening is deterministic and session/profile-scoped. Independent questions, decisions,
  allowed answers and original evidence survive grouping. A chain of overlaps cannot grow one
  context beyond 45 seconds, except an original individual question already longer than that.

## Real Replay

The three-session private baseline freezes raw CAF, session metadata, resolved dialogue and saved
ASR JSON by SHA-256. It also retains the pre-change rich transcript. The checker verifies source
conservation, current immutable JSON/Markdown agreement and unchanged speaker labels/coverage.
It checks the production transcriber and strict publisher against their unchanged policy pins.

| Case | Utterances | Interval warnings | Existing micro warnings | First cached finalization |
| --- | ---: | ---: | ---: | ---: |
| A | 703 | 6 | 95 | 11.00s |
| B | 732 | 4 | 88 | 9.77s |
| C | 408 | 5 | 23 | 6.94s |

Warnings overlap existing questions, so counts are not additive. A's queue grows from 151 to
156 questions, with 117 listening contexts; attributed remote coverage stays 54.9819%. These
are coverage/metadata measurements, not word or speaker accuracy. Playback was not manually graded.
The normal `murmurmark transcript SESSION --path-only` command also returned the current attributed
generation for all three sessions, retaining the provisional-attribution disclaimer.
The finalization measurement is not an end-to-end meeting benchmark and includes raw identity
verification; optional inference and full review pack creation are excluded.

## Cache Canaries

| Case | Clips | Unique PCM | Extra identical PCM copies | Conflicting saved decode groups |
| --- | ---: | ---: | ---: | ---: |
| A | 784 | 364 | 420 | 0 |
| B | 735 | 324 | 411 | 0 |
| C | 245 | 118 | 127 | 0 |

Two bounded real whisper.cpp cold/warm pairs per session all passed: exactly one decode per pair,
identical relative output, unchanged inputs. Cold times were 1.330-2.742s; warm times 0.007-0.020s.
The existing resource profile, nice and thread limit were retained. Duplicate files do not establish
the number of historical model calls, and six short canaries do not qualify a production producer.

The reproducible qualification result remains **DO_NOT_PROMOTE**. Raw pairs are unavailable for
11/12 old Echo v2.17 sessions. Replacement controls, full producer replay, independent lexical
truth and independent voice truth remain required for their respective promotions. Frozen policies,
production ASR and strict speaker selector are unchanged.

## Reproduction

With the private baseline already present:

```bash
.venv/bin/python scripts/check-transcript-evidence-repair-corpus.py \
  --baseline sessions/_reports/transcript-evidence-repair-2026-10-06/baseline.json \
  --out sessions/_reports/transcript-evidence-repair-2026-10-06/replay.json \
  --check-published
```

For a new baseline, pass repeated `--session SESSION` and a new output directory. A baseline is
never overwritten; baseline and output cannot be the same file. Only explicitly refreshed sessions
should be checked with `--check-published`. The baseline also supplies the normal automatic
retention pin while this qualification remains active; retire it deliberately after closure.

Focused checks: `check-transcript-publication.py`, `check-review-materialization-guard.py`,
`check-review-audio-evidence.py`, `check-review-publication.py`,
`check-review-listening-contexts.py`, `check-session-state-reconciliation.py`, and
`check-meeting-lifecycle.py`. The new listening check is part of `scripts/check.sh`.

## Release Validation

The final full run exited with zero:

```bash
env OPENBLAS_NUM_THREADS=3 OMP_NUM_THREADS=3 MKL_NUM_THREADS=3 \
  nice -n 20 scripts/check.sh
```

This includes Swift build and 17 Swift tests, SwiftLint, Python compilation and regressions,
capture static checks, sidecar/resume smokes, CLI acceptance, release-bundle acceptance and the
final fixture smoke. Separate planning consistency, public-content and diff checks also passed.
Live capture and a full fresh ASR/lifecycle benchmark were not repeated for this repair.

Two preparation failures preceded the successful run: a launcher `OMP_NUM_THREADS=1` contradicted
the resource-policy fixture's two-thread configuration; subsequently, release packaging omitted
the new helper before it was added to the Git index. A three-thread test cap and staging the new
files resolved these respectively. No fixture, production resource policy or release check was weakened.
