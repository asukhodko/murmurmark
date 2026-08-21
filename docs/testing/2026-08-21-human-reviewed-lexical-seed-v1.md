# Human-Reviewed Lexical Seed v1: Frozen Queue

Date: 2026-08-21

Decision: `REVIEW_REQUIRED`

## Frozen Evidence

- two real sessions: one group call and one 1x1;
- acoustic modes: `headphones_or_low_leak` and `speaker_playback`;
- roles: `Me` and remote;
- 24 primary slots: six per session-role cell;
- four blind repeat slots;
- exact raw, transcript, speaker-profile, policy, implementation and clip SHA-256 fingerprints;
- production hypotheses are absent from the review queue.

The queue currently has `0/28` answers. WER/CER and domain-term accuracy are intentionally empty
until direct human text exists. The public snapshot records this limit rather than presenting model
agreement as accuracy.

## Verification

- synthetic end-to-end check passes exact truth, repeat consistency, privacy, stale-input rejection
  and byte-exact replay;
- real preflight validates both meeting and acoustic modes;
- frozen real report replays byte for byte;
- raw mic and remote CAF SHA-256 values are unchanged before and after materialization;
- no selected transcript, Coverage v3 artifact or ASR cache was modified.

## Next Evidence

Run `murmurmark corpus lexical-seed-v1 review`. When all slots are answered, regenerate the tracked
snapshot and classify the result as `REFERENCE_READY` or `EVIDENCE_BOUND`.
