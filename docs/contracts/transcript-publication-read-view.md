# Transcript Publication Read View

Updated: 2026-10-01. Scope: provisional and unavailable attributed read views only.
The frozen strict publisher, primary ASR producer, base dialogue and policies are unchanged.

## Source And Display

`utterances` retain source text, roles, timestamps and quality. Additive `display_turns` contain
`utterance_id`, `parent_position`, `turn_index`, `speaker_label`, `text`, `start`, `end`,
`source_interval`, `time_basis` and `review_reasons`. Markdown renders those same rows.
The concatenated child text must exactly match its parent; otherwise the parent text is rendered
with a speaker warning. A bad child interval invalidates timing precision for the whole parent,
preserving word order and labels. Known display starts are sorted globally so an intervening Me
turn can appear between two remote turns. Missing time is `??:??`, never an invented zero.

`time_basis` distinguishes `speaker_turn`, `utterance`, `parent_interval`, `unknown`,
`acoustic_lower_bound` and `unsupported_audio_time`. Parent fallback and sound bounds are
approximate. None modifies source timing, overlap gates, speaker eligibility or primary ASR.

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

Publication fingerprints include renderer, micro evidence helper, acoustic helper and raw identity.
Both immutable JSON and Markdown are written before the selection pointer. Verification rejects
stale code/audio and ordinary read commands do not run inference or rewrite session artifacts.
Coverage remains label coverage, not measured accuracy. Strict promotion/export gates are unchanged.
