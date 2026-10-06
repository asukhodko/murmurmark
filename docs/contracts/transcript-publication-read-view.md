# Transcript Publication Read View

Updated: 2026-10-06. Scope: selected, provisional, unavailable and fallback reading projections.
The frozen strict publisher, primary ASR producer, base dialogue and policies are unchanged.

## Selected Reading Projection

After independently verifying the existing speaker selection, `evaluate-outcome.py` publishes
`derived/transcript-rich/read-view-v1/generations/<digest>/transcript.read.json/.md`. The default
`murmurmark transcript` selects this projection only while source identities, current outcome,
queue snapshot and renderer identities agree. Read commands never generate it or run inference.
The CLI verifies the captured generation ID and opens that immutable file, not a later pointer.
Missing/stale projection falls back to the verified source with an explicit CLI warning.
`--aggregate`, explicit profiles and reviewed-name rich views keep their existing semantics.

The projection uses every currently pending question from the fingerprint-bound review snapshot,
including questions created after the original source quality flags. Markdown links each question
to its independent ID/reason in an appendix. Questions without matching utterances stay explicitly
unplaced in that appendix. Unresolved historical decisions from older profiles are likewise retained
there with their original profile, never attached to current utterances just because IDs match.
A closed question is not recreated from a historical source flag. `needs_review` is an answered but
still unresolved question; open-question totals are not the progress command's unanswered totals.
Missing/stale queue means unknown completeness, not zero remaining work. Display-time conflicts
remain separate warnings even if the review queue is empty.

The header exposes outcome, use/export gate, speaker state/fallback and deferred checks separately.
Source `utterances`, text, labels and speaker eligibility are unchanged. Strict attribution does not
certify lexical accuracy. Publication cannot close a question or unblock export. JSON/Markdown are
written before an atomic selection pointer; interrupted publication preserves the previous pointer.

## Source And Display

`utterances` retain source text, roles, timestamps and quality. Additive `display_turns` contain
`utterance_id`, `parent_position`, `turn_index`, `speaker_label`, `text`, `start`, `end`,
`source_interval`, `interval_provenance`, `time_basis` and `review_reasons`. Markdown renders those same rows.
The concatenated child text must exactly match its parent; otherwise the parent text is rendered
with a speaker warning. A bad child interval invalidates timing precision for the whole parent,
preserving word order and labels. Known display starts are sorted globally so an intervening Me
turn can appear between two remote turns. Missing time is `??:??`, never an invented zero.

`time_basis` distinguishes `speaker_turn`, `utterance`, `parent_interval`, `unknown`,
`acoustic_lower_bound` and `unsupported_audio_time`. Parent fallback and sound bounds are
approximate. Parent fallback is shown as `~start-end`, never a precise repeated start. Intersections
link the involved utterance IDs without claiming simultaneous speech or exact word ordering.
None modifies source timing, overlap gates, speaker eligibility or primary ASR.

## Acoustic Bound

`murmurmark.acoustic_timing_read_view/v1` inspects only a canonical single remote CAF with known
source bounds, at most 120 seconds per utterance. A prefix of at least two seconds of exact zeros
on every channel can show that a phrase cannot precede the first nonzero sample. The original
ASR source prefix is checked too: earlier audio invalidates this proof. Nonzero background sound,
quiet speech and opposite-phase stereo are not silence. This is not VAD, speech onset, word
alignment, role verification or evidence that every word is correct.

Each finding includes original and checked intervals, first nonzero frame, sample rate and
`word_alignment_established=false`. An all-silent window keeps the source time with a warning;
it does not move or delete the text. Missing, unreadable or multi-file raw leaves the display
unchanged. Audio hashes before/after must agree; changes invalidate the finding.

## Review And Provenance

Open quality reasons appear next to the affected words, with separate text/role/speaker/time
facets. `cleared` subtrees are historical, not open issues. A short micro-ASR segment extending
outside its target by more than 250ms cannot establish ownership of the full context phrase;
complete selected-word bounds can support ownership, not voice identity or lexical accuracy.
Agreement between raw and filtered mic does not override a scope conflict. No automatic text
replacement, deletion or role change follows from these warnings.

`interval_provenance` separately records original source bounds, the recognition slice (including
micro-ASR padding where available), selected bounds and source candidate/repair. The legacy
`display_turns.source_interval` keeps its previous meaning; it must not be mistaken for the original
ASR interval. Missing metadata stays null, not an invented time.

The shared `interval_ownership_v1` check covers ordinary Me and remote candidates, even with empty
repair metadata and `quality.needs_review=false`. A refinement exceeding 250ms without complete
selected-word support requires review when there are at least four words and either more than
24 non-space normalized characters/second, or a duration reduction of at least one third with
five or more words/second. There is no 1.25-second cutoff. These are conservative triage thresholds,
not acoustic truth. Ordinary silence trimming and short acknowledgements do not automatically
become review tasks. Provenance remains visible even when the heuristic raises no warning.

The same `text_interval_narrowed_without_word_support` reason reaches JSON, Markdown and the full
text-review queue. Suggested keep/drop cannot close that question; preserving a hypothesis is not
verification of its time ownership. Remote questions never gain an automatic deletion action.

Publication fingerprints include renderer, micro/interval evidence helpers, acoustic helper and raw identity.
Both immutable JSON and Markdown are written before the selection pointer. Verification rejects
stale code/audio and ordinary read commands do not run inference or rewrite session artifacts.
Coverage remains label coverage, not measured accuracy. Strict promotion/export gates are unchanged.

## Complete Review Queue

`operational_readiness_report.json.review_queue` includes every deduplicated mandatory row, after
the existing low-materiality exclusions. `--max-review-items` limits only `review_queue_preview`
and Markdown; summary fields `review_queue_preview_items` and `review_queue_preview_omitted`
make the difference explicit. A limit of zero hides the preview, not the work.

`build-review-plan.py --max-clusters` likewise limits only the Markdown preview. `clusters`,
`review_decisions.template.jsonl`, lane totals and listening totals cover the complete queue.
`cluster_preview_count` is separate from `cluster_count`. A preview is never an input to closure.

Queue snapshot provenance includes the readiness and plan producers as well as the progress reader,
template, decisions and selected dialogue. Snapshots from the old truncated producers are stale until
explicit reconciliation. Rebuilding can increase the visible remaining count without changing audio
or text. Sum of question intervals and union of audio intervals remain distinct metrics.

## Shared Listening

The workspace includes `listening_contexts`: overlapping questions within the same session and
input profile share playback, not decisions. Each retains its question ID, source interval,
utterance IDs and allowed answers. Source/recognition context is included when it fits within
45 seconds; broader original candidates stay in provenance and use bounded target playback.
A single long question is not silently split. Closed questions are omitted without closing their
neighbours. Mic, remote and available cleaned tracks are offered separately.

`build-review-workspace.py --metadata-only` writes `review_listening_contexts.json/.md` without
rebuilding audio or replacing the full workspace and answer sheets. This index cannot be applied
as an answer sheet. Full workspace construction remains available for explicit review.
