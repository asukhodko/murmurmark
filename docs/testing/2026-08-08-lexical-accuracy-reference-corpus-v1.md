# Lexical Accuracy Reference Corpus v1 Result

Date: 2026-08-08

Decision: `REFERENCE_INSUFFICIENT`

## Evidence

The frozen corpus contains nine graded sources:

- one exact generated digital-source cycle;
- six expected Echo Lab prompt groups from held-out dev and hard-test sessions;
- one independent machine transcript for a 1x1 meeting;
- one independent machine transcript for a group meeting.

The exact generated subset contains 67 words and scores WER `0.0`, CER `0.0`, with zero
substitutions, deletions and insertions. All three generic term checks pass. This proves the current
local whisper.cpp path on that bounded clean digital source only.

The scripted local-only rows show low disagreement (`0.041096` dev, `0.027397` hard test), while
double-talk rows are dominated by remote speech and exceed WER `1.5`. These values are useful
diagnostics, but the operator may speak late, repeat or omit prompts; they are not ground truth.

The two external sources cover both 1x1 and group shapes and both roles. They are independent
machine transcripts, so their interval-aligned disagreement cannot establish which recognizer is
correct. The damaged group capture has only `10.7335%` shared reference intervals; that limitation is
recorded explicitly.

## Exact Evidence Limit

No human-reviewed real-meeting word reference is available. Missing promotion coverage is:

- two human-reviewed sessions;
- 1x1 and group meeting modes;
- `Me` and remote roles;
- speaker playback and headphones/low-leak acoustic modes.

Weak sources are excluded from correctness by policy and test. Therefore no real-meeting WER, no
largest proven lexical defect and no ASR change can be justified from the current evidence.

## Non-Regression

- selected dialogue and Markdown hashes are unchanged for both imported sessions;
- Speaker-Resolved Default selection is unchanged where present;
- all four raw CAF hashes are unchanged;
- public and tracked artifacts contain no transcript text, speaker names or absolute local paths;
- replay is byte-deterministic;
- Transcript Perfection verifies 13/13 sources and reports
  `recognized_words.bounded_exact_subset_only` with the real-meeting reference blocker intact.

## Next Engineering Direction

Human-Reviewed Lexical Seed v1 is an external-evidence prerequisite, not a justified autonomous
repair. The next agent-executable quality goal should use a genuinely independent local remote
speaker backend against the already frozen `598.240s` unknown-speaker residual. It must not weaken
Coverage v3 thresholds or treat another Whisper transcript as truth.
