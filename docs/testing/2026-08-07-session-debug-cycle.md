# Session Debug Cycle: 2026-08-07_15-01-22

Date: 2026-08-07

This real-session debug cycle froze raw capture, reproduced three independent defects and verified
the fixes without changing transcript text automatically.

## Frozen Inputs

| Track | Size | SHA-256 |
|---|---:|---|
| `audio/mic/000001.caf` | 666409108 bytes | `40152895ae25dedead040c8b6084f5d8dd46824114d43ccbd6a19a58543e42c2` |
| `audio/remote/000001.caf` | 1332833312 bytes | `b32f53a42b1851e30062199002a126a6daa72e8fa9938f252e0516dbcb275a51` |

The meeting lasted `3470.762s`; mic and remote raw tracks cover `3470.859s` and `3470.909s`.
All `116` authoritative ASR chunks were present.

## Findings

1. Three ScreenCaptureKit restarts inserted measurable PCM gaps. The bounded continuity audit found
   `3` gaps / `2.465666s`; the largest was `0.910479s`, only `0.0710%` of the meeting. The session
   remains usable and is not classified as partial.
2. Two open `Me` review rows were previously vulnerable to a false faster-whisper `keep_me` because
   mic ASR repeated remote words. Both intervals are fully covered by `remote_only` speaker state.
3. The outcome called advanced pre-ASR echo selection `missing` without showing that the actual ASR
   input was valid `local_fir_role_masked` selected by the production policy.

## Changes And Evidence

- `murmurmark.capture_continuity/v1` now measures restart-correlated PCM gaps and feeds
  `status`, readiness and outcome.
- The stronger audio judge uses interval-weighted speaker state. A remote-only interval with weak
  local support and mic-to-remote agreement vetoes `keep_me` and fails open to `needs_review`.
- Readiness exposes `pre_asr_echo_active_*` separately from `pre_asr_echo_advanced_*`.

Target-Me WavLM evidence independently remained ambiguous for both rows because mic and remote
voice scores were nearly equal. The safe result is therefore explicit uncertainty, not deletion.

## Result

- selected transcript profile: `reviewed_v1`;
- verdict: `usable_with_review`;
- local-only island recall: `0.965517` (`56/58`), with no independently supported lost `Me`;
- suggested review closed `15/17` rows, including `14` keeps and one safe drop;
- remaining mandatory review: `2` rows / `5.45s`;
- capture continuity: `warning`, `partial_recommended: false`;
- active pre-ASR echo profile: `local_fir_role_masked`;
- advanced personalized echo selector: not evaluated for this session.

The remaining two rows are intentionally unresolved. Available local evidence does not justify an
automatic keep or drop, and the system now states that limit directly.
