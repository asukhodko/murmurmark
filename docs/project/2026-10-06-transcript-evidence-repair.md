# Transcript Evidence Repair

Updated: 2026-10-06. Status: operational repair implemented and verified;
acoustic/performance qualification remains open.

## Scope And Acceptance

Continue Bounded Evidence Compute with a general text/interval ownership check,
bounded cached finalization, qualified exact-PCM reuse and separate acoustic
selection/attribution experiments. Keep capture, Echo tuning, resource settings,
raw, production fallback and private meeting content unchanged.

1. Freeze available inputs and selected outputs before replay. Private manifests
   and case details belong under ignored `sessions/_reports/`, never in Git.
2. General interval checks cover both roles and ordinary candidates, not only
   micro-ASR. JSON, Markdown and the full queue retain the same unresolved reason.
3. Finalization uses completed evidence without inference, preserves the last
   valid publication and exposes incomplete work within its existing deadline.
4. Cache keys bind exact PCM and decode configuration, never consumer labels.
   Producer integration needs replacement-corpus qualification; missing evidence
   means DO_NOT_PROMOTE, not a policy hash update on trust.
5. Word selection and long-remote attribution remain separate experiments until
   conservation and acoustic truth gates pass. Coverage alone is not accuracy.
6. Related review questions share listening context, not decisions. Run targeted
   and full regression tests, synchronize planning and commit/push checked work.

## Checkpoint

- Clean baseline: `b33e457`; no running session processing at start.
- Confirmed ordinary local and remote refinements retain complete text without
  an ownership warning. The short micro-ASR case is already warned, not repaired.
- Three private sessions now have immutable baseline manifests and read-only replay checks.
  They preserve source ASR/raw hashes, text, roles, source times and compatible labels.
- General checks and provenance cover ordinary candidates of both roles. The initial broad
  all-refinement warning was rejected during replay: silence trimming is common. Mandatory
  review is limited to the explicit density/materiality heuristics in the
  [publication contract](../contracts/transcript-publication-read-view.md).
- Final meeting refresh skips lane audio and decision rebasing, uses cached speaker evidence
  and retains the existing 30-second bound. Three real cached passes completed in 11.00, 9.77
  and 6.94 seconds. The most recent 85-minute case retains 54.9819% attributed remote coverage;
  its queue includes five previously omitted questions (151 to 156).
- Related questions share bounded playback, not answers. The latest case has 117 listening
  contexts for 156 questions. Metadata-only refresh leaves full lane packs and answer sheets intact.
- Six real-model cache canaries passed: one decode per pair, identical relative output,
  cold 1.330-2.742s and warm 0.007-0.020s. No full-session speedup is claimed.
- The full `scripts/check.sh` run passed, including Swift, Python regressions, CLI acceptance,
  release-bundle acceptance and fixture smoke. Planning and public-content checks passed too.

Verification details and reproduction: [test report](../testing/2026-10-06-transcript-evidence-repair.md).

## Remaining Qualification

The full acoustic/performance series is not closed by this operational repair. Its production
decision is **DO_NOT_PROMOTE** for cache integration, new lexical selection and cluster-gate changes.
The following work remains ordered, not silently cancelled:

1. Qualify an explicit replacement producer corpus. Only one of the twelve old Echo v2.17 raw
   pairs is still present. The three frozen replay sessions and six cache canaries do not replace
   the existing no-speech, headphones, double-talk, local-content and exact-fallback controls.
   Verify those scenarios and full cold/warm output conservation before changing any policy hash.
2. Qualify word-bounded micro-ASR selection separately from the cache. Recognition-slice bounds,
   target bounds and exact word support must remain distinct. Wider context or two similar mic
   variants cannot prove that all words belong to a sub-second target. Preserve short answers,
   interruptions and negative words; keep unsupported alternatives under review. This repair
   adds detection/provenance, not an unqualified replacement transcript.
3. Evaluate long-remote subdivision and localized cluster instability with independent voice
   evidence. Keep existing provisional labels and unknown reasons. Higher label coverage alone
   cannot justify removing the whole/chunk stability gate or naming speakers by conversational meaning.
   The new rendered warnings cover provisional/unavailable read views; the frozen strict
   publisher is unchanged. Its extension also needs explicit compatibility qualification.
4. Finish the broader Bounded Evidence Compute goal only after production qualification or a
   reproducible final evidence-bound decision. Do not mark word selection/diarization solved merely
   because finalization is now fast. Summary/note interpretation remains outside this work.
