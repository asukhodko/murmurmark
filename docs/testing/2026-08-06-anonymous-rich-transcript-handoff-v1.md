# Anonymous Rich Transcript Handoff v1

Status: `PROMOTE_OPTIONAL_RICH`

The handoff turns passing Remote Speaker Evidence Map v1 output into a stable optional transcript
view. It preserves the current Evidence Handoff v2 utterance array and attaches session-local
anonymous evidence by remote utterance ID.

## Implementation

- `scripts/materialize-anonymous-rich-transcript.py` validates policy, source corpus, model,
  parameters, implementation, current audit artifacts and current selected handoff fingerprints.
- Publication uses an fsynced immutable bundle followed by one atomic current-manifest update.
- `murmurmark audit remote-speakers SESSION` builds the map and attempts optional materialization.
- `murmurmark transcript SESSION --rich` verifies only; it cannot regenerate stale evidence while
  reading and it never replaces the ordinary transcript.
- `scripts/check-anonymous-rich-transcript.py` covers exact references, newer selected `Me` content,
  aggregate fallback, deterministic replay, stale inputs, remote mismatch, forbidden names,
  unapproved model evidence, missing policy and interrupted publication.

## Frozen Corpus

The tracked manifest is `docs/testing/anonymous-rich-transcript-v1-manifest.json`. It fingerprints
the policy, materializer, reporter, predecessor corpus, per-session inputs and every published rich
output.

| Metric | Result |
|---|---:|
| Sessions passed | 6/6 |
| Selected utterances | 2319 |
| Remote references | 1235 |
| Attributed remote utterances | 629 |
| Aggregate fallback utterances | 606 |
| Session-local anonymous speakers | 14 |

Every session passed exact selected-dialogue, exact remote-reference, anonymous-label,
byte-identical replay and ordinary-output non-regression gates. A second complete run matched the
tracked frozen manifest byte for byte.

## Decision

`PROMOTE_OPTIONAL_RICH` exposes only the explicit `--rich` read surface. It does not alter plain
Markdown, notes, verdict, Evidence Handoff v2, transcript auto-selection, guarded export or
retention. Unsupported remote utterances remain aggregate `Colleagues`.

Names and cross-session identity remain forbidden. The next bounded stage is Reviewed Remote
Speaker Naming v1, where labels can enter only through an explicit fingerprint-bound local review
decision.
