# Current Goal

Status: current

Updated: 2026-08-06

The stable product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`. Raw
CAF and batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded
production audio profile. Evidence Handoff v2 remains the only input to guarded export.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Authoritative Incremental ASR v1

OpsKarta nearest goal: Authoritative Incremental ASR v1: сократить задержку свежего пути `first Ctrl-C -> authoritative transcript` за счёт точного и fail-open переиспользования ASR-окон, вычисленных во время durable capture или предыдущей прерванной обработки; различать cold/cache/live-origin evidence, принимать кэш только при совпадении PCM, окна, модели, prompt и decode options, не менять raw CAF, основной whisper.cpp, selected transcript или quality gates и завершить corpus-wide PROMOTE/DO_NOT_PROMOTE с измеренным пределом.

## Why This Is Next

Reliable Final Handoff v1 closed the lifecycle-control failure. Its frozen three-session
cache/resume verification has p90 post-stop ratio `0.059041`, zero dead ends, zero stale handoffs,
zero unexplained overruns and exact Speaker-Preserving Neural Echo reuse on `2/2` applicable
sessions. A compatible `process --skip-build` now returns through the same handoff checkpoint, and
`status` reports either completion or bounded human decisions.

That evidence does not measure a fresh whisper.cpp run. The pre-change corpus still shows p90
post-stop ratio `1.502`, and the August 5 outliers spent most of their time in baseline ASR. The next
mission-critical limit is therefore recognition throughput, not richer remote-speaker semantics.
Remote Speaker Evidence Map v1 remains next after this gate.

## Objective

Turn already completed, byte-identical ASR work into an authoritative batch cache without treating
provisional live text as truth. A fresh run, interrupted run and live-origin run must remain
distinguishable in provenance. If exact compatibility cannot be established, ordinary batch ASR
runs unchanged.

## Required Work

1. Freeze a cold/cache/live-origin latency matrix with raw, canonical ASR-audio and transcript
   hashes. Never compare cached timing with cold timing as one population.
2. Define one canonical chunk identity over role, start/end samples, overlap policy, PCM SHA-256,
   sample rate/channels, whisper.cpp binary/model, language, prompt and every decode option.
3. Extend the current chunk cache so each row records origin, identity, completion state and exact
   replay proof. Partial writes and stale manifests are invalid.
4. Reuse committed live or interrupted-run chunks only after post-stop canonical audio is
   materialized and its chunk identity matches exactly. No text-similarity reuse.
5. Preserve deterministic overlap reconciliation, word timestamps and raw segment provenance. A
   mixed cache/recompute transcript must equal a clean full recompute byte for byte.
6. Keep cache production best-effort and bounded. Queue lag, worker failure or missing live evidence
   must not affect durable capture or delay fallback batch ASR.
7. Use the configured work-conserving low-priority profile after capture, expose useful progress/ETA
   and account separately for reused and decoded audio seconds.
8. Add fixture, corruption, prompt/model mismatch, interrupted-write, overlap-boundary, capture
   fail-open and frozen-corpus tests. Make a measured `PROMOTE` or `DO_NOT_PROMOTE` decision.
9. Reconcile README, contracts, runbooks, current goal, roadmap and OpsKarta; commit and push the
   complete result.

## Acceptance Gates

- every reused chunk has an exact canonical identity and immutable provenance;
- changed PCM, model, prompt, language, decode option or window contract causes recomputation;
- mixed replay output is byte-identical to a clean full batch recompute;
- raw CAF hashes, capture health and first-handoff quality gates do not change;
- failed or lagging sidecars leave no stale success and fall back automatically;
- on at least three applicable real sessions, authoritative post-stop time improves by at least
  `50%` versus their own cold baseline, or the goal ends in reproducible `DO_NOT_PROMOTE` with the
  exact compatibility/runtime ceiling;
- stable sessions without live evidence regress by no more than `10%` in post-stop runtime;
- local recall, chronology, remote-like `Me`, protected content, selected profile, notes evidence and
  guarded export do not regress;
- repeated frozen runs are deterministic and all required checks pass.

## Safety Boundary

- no raw mutation, second capture process, cloud service or new primary ASR;
- no live transcript promotion and no semantic/text-similarity cache acceptance;
- no quality-threshold relaxation to gain speed;
- no Echo, Target-Me, remote-speaker diarization, naming, LLM synthesis or UI changes;
- cache failure is a performance loss only, never a transcript-quality shortcut.

## Definition Of Done

- versioned identity/cache contracts and implementation exist;
- cold/cache/live-origin reports are frozen and reproducible;
- corpus decision is explicit, with latency and quality evidence;
- operator-facing progress, fallback and resume behavior are documented and tested;
- README, contracts, runbooks, roadmap and OpsKarta agree with the measured result;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Completed Predecessor

Reliable Final Handoff v1 completed on 2026-08-06. Its implementation separates optional enrichment
from the first handoff, enforces machine-readable budgets, applies only fresh safe suggested review,
emits bounded manual decisions, refreshes stale lifecycle reports, preserves raw identities and
provides a deterministic lifecycle corpus gate. The exact pre/post evidence is in
`docs/testing/2026-08-05-reliable-final-handoff-baseline.md`.
