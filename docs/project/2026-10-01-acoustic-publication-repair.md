# Acoustic Publication Repair

Updated: 2026-10-01. Status: publication/review checkpoint verified; upstream qualification open.

## Scope

Continue the September 30 reliability repair with explicit local uncertainty, safe
speaker-turn timestamps, acoustic timing support and bounded micro-ASR selection.
Then qualify exact-input cache reuse without changing resource limits. Capture,
Echo tuning, raw audio, human naming and live promotion remain out of scope.

## Work Log

- Baseline: existing uncommitted capture changes are separate and must survive.
- Confirmed: missing nested start becomes zero; local review flags are absent from
  Markdown; wide micro-ASR segments can be assigned to a shorter Me island by their
  midpoint; remote timing can cover digital silence; lexical review misses remote
  anomalies; repeated micro decodes waste compute.
- Implemented: immutable display projection shared by JSON/Markdown, per-turn reasons, safe
  parent/unknown fallback and globally ordered turns without losing source words or voice labels.
- Implemented: exact digital-silence prefix evidence. Display starts cannot precede sound in the
  checked source window; the lower bound is explicitly approximate, never a word alignment.
- Implemented: context ownership checks for successful micro-ASR; legacy `stable` cannot bypass
  them. Text questions survive adjacent chronology questions. Remote review retains remote IDs
  and has no automatic keep/drop-Me decisions.
- Three cached corpus replays completed without primary ASR; raw, base dialogue, source quality
  and historical run reports stayed unchanged. Ordinary read commands did not write artifacts.
- Strengthened corpus assertions cover exact text conservation, display ordering, JSON/Markdown
  agreement and read-only CLI behavior. A separate staged snapshot passed build and targeted tests.
- Release verification: debug/release builds, self-test, regression and acceptance checks passed
  by stage. The final smoke passed after updating its obsolete text-queue count assertion.
  The [verification report](../testing/2026-10-01-acoustic-publication-repair.md) records the exact
  run boundaries. The scoped release excludes pre-existing capture edits and their tests.

## Qualification Boundary

The frozen transcriber, strict selector and policy hashes are unchanged. Eleven of twelve old
Echo v2.17 qualification sessions lack raw, so replacing a hash would not establish safety.
This release improves the provisional read view and review detection. It does not resolve lexical
accuracy, speaker cluster purity, upstream remote intervals before overlap/Me repair, or prevent
the old producer from generating a broad-context micro hypothesis. Such a hypothesis is retained
and marked for review. Strict publication is still handled by its frozen renderer.

The next qualification must freeze an available replacement corpus and compare word alignment,
word-bounded micro selection and exact-input cache against the current producer. Cache integration
remains `DO_NOT_PROMOTE` until that evidence exists. Do not describe a read-view timing improvement
as an upstream chronology repair or a measured processing speedup.

## Release Gates

1. Raw and primary ASR evidence remain unchanged; source hypotheses are retained.
2. Missing or conflicting time never silently becomes a precise zero timestamp.
3. Every unresolved review reason survives publication next to its source text.
4. No whole context phrase becomes confirmed Me merely by midpoint overlap.
5. Genuine short speech, intentional repetition and double-talk are conserved.
6. Existing voice labels survive when their actual dependencies remain valid.
7. Frozen producer/policy fingerprints change only after explicit qualification.
8. Tests distinguish publication, acoustic correctness and independent voice truth.
9. Cache optimization preserves decode configuration and consumer provenance.

Private meeting excerpts and identifiers belong in local `sessions/_reports/`,
not public test fixtures. Insufficient acoustic or speaker evidence remains open.
