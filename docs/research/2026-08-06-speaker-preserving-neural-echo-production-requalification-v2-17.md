# Speaker-Preserving Neural Echo Production Requalification v2.17

Status: complete

Decision: **PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2**

Updated: 2026-08-06

## Why Requalification Is Required

The promoted v2.16 production policy pins the exact primary transcriber runtime. Two later changes
made that policy intentionally incompatible:

- authoritative incremental whisper.cpp chunks and their reconciliation contract were added;
- a deterministic non-speech music-caption filter was added.

The v2.16 policy therefore fails open to the exact `local_fir_role_masked` baseline. Replacing its
transcriber SHA-256 would assert compatibility without measuring it and is forbidden.

## Frozen Contract

v2.17 is a contract-only requalification. It keeps these properties unchanged:

- the v2.15 audio algorithm and all selector thresholds;
- the v2.16 hard and corpus session membership;
- exact `local_fir_role_masked` fallback;
- zero promotion credit for post-ASR cleanup;
- immutable raw CAF, v2.16 reports and v2.16 decisions.

The new policy additionally pins `scripts/authoritative_asr_cache.py`. The hard and corpus manifests
freeze current input files, the current whisper.cpp model, both ASR runtimes and the immutable v2.16
evidence. The post-lock session `2026-08-06_16-59-51` is regression evidence only and cannot change
thresholds or promotion gates.

Older research policies keep their original production-policy SHA as historical evidence. Their
integrity checks accept v2.17 only as an explicit contract-only successor: the promoted profile,
base selector files, algorithm revision, zero threshold change and exact fallback must all match.
The old SHA values and reports are not rewritten.

## Evaluation

The isolated profile is `speaker-preserving-neural-echo-v2-17`. It uses current chunked primary ASR
without reading pre-v2.17 micro-ASR cache. The one-shot order is:

```text
seal hard -> seal corpus -> lock candidate -> run hard once
  -> if hard passes: run corpus once
  -> PROMOTE or DO_NOT_PROMOTE
```

Hard requires terminal speaker dispositions, an exact no-speech fallback, bounded runtime and zero
post-ASR credit. Corpus promotion retains the v2.16 utility gates: at least two candidate sessions,
at least `5s` and six remote-supported tokens removed, exact local-token retention, exact headphones
fallback and bounded runtime.

Generated evidence stays under ignored session reports:

```text
sessions/_reports/speaker-preserving-neural-echo-v2-17-hard/
sessions/_reports/speaker-preserving-neural-echo-v2-17-corpus/
sessions/<id>/derived/preprocess/speaker-preserving-neural-echo-v2-17/
```

## Production Boundary

Production accepts both policy schemas during migration, but dynamically loads only the selector
whose path and SHA-256 are pinned by the active policy. Candidate audio is taken from the signed
selection report and must resolve inside the session. Publication remains transactional. Any
missing artifact, changed hash, unsafe selector result or publication error restores the exact FIR
baseline.

Readiness and outcome now retain:

- pre-ASR selection status, profile, reason, compatibility and exact-fallback state;
- residual remote evidence from overlap audit, transcript duplication and audio review;
- `partial` evidence coverage when remote-forbidden audit is missing or skipped.

Overlapping risk estimates are combined by maximum, not summed. A zero from one audit can no longer
hide positive evidence from another, and an uncovered zero remains `unknown` rather than `pass`.

## Decision

The one-shot hard and corpus runs passed without changing the audio algorithm or thresholds:

| Check | Result |
| --- | ---: |
| hard sessions | `3/3` |
| hard decision | `HARD_TEST_PASSED_V2_17` |
| corpus sessions | `12/12` |
| candidate sessions | `5` |
| exact fallback sessions | `7` |
| remote-supported reduction | `41.940s` |
| remote-supported tokens removed | `90` |
| candidate local-token retention | `1.0` |
| maximum hard runtime factor | `0.781844` |
| maximum corpus runtime factor | `0.524864` |
| post-ASR cleanup promotion credit | `0` |

The five candidate sessions and all utility values match the v2.16 decision. The current chunked
ASR and music-caption filter therefore preserve the promoted selector's safety and utility. The
tracked production policy now uses schema v2.17 and pins the new evidence.

```text
hard fingerprint:
  62e5d87749110e805808716412f1d2613c439901022f9be627502bc4d3334510
hard report:
  9508ec06028070798c71cd13ecfa5d02f0aa02ab46a43655e2abb8dc402aa173
corpus fingerprint:
  03baa8b0095be465cf423baa88934dbe33cc7f8f5635cf9bff253b6a7200cb1b
corpus report:
  4af7e659d91a43eaf5da6bbdc5c916661612696081a4e1b6113864c991aa96ae
```

This restores compatibility; it does not claim a better echo algorithm than v2.16. The next audio
frontier remains the separately gated four-stem Target-Me separator.

## Post-Lock Production Regression

Session `2026-08-06_16-59-51` was processed only after the v2.17 policies and thresholds were
locked. All production-policy checks passed and the audio candidate was evaluated, but its complete
shadow transcript failed the existing session gates. Production therefore selected
`local_fir_role_masked` with reason `session_selector_fallback` and `exact_fallback: true`.

The three canonical mic inputs match their frozen baseline snapshots byte-for-byte. Raw capture
hashes remained unchanged: mic
`598f1df8bc500b89ec161f6219833b91fbd441a2f3e0d9bd7bb9f090253f2df1`, remote
`1c0c814d3f1d8796726b40ce13566daeb066d8a114a753083210714cc0fd2650`. Refreshed readiness and
outcome show the fallback explicitly. They also report `43.08s` harmful remote-in-`Me` evidence
with `partial` coverage instead of the previous false zero. The result validates both sides of the
contract: v2.17 is usable by current production, while a session-level safety failure still
restores the exact FIR baseline.
