# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded
production audio profile. Evidence Handoff v2 remains the only input to guarded export.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Canonical Live ASR Producer v1

OpsKarta nearest goal: Canonical Live ASR Producer v1: снять измеренный blocker Authoritative Incremental ASR v1: научить capture-safe Live Shadow производить полностью завершённые canonical 60s/5s mic и remote chunks с exact PCM/model/prompt/decode identity и `authoritative_live_asr_chunk/v1`, чтобы post-stop batch мог безопасно переиспользовать их; сохранить raw CAF и batch authoritative, fail-open при lag/corruption и добиться не менее 50% сокращения свежего post-stop ASR на трёх реальных сессиях либо завершить воспроизводимым DO_NOT_PROMOTE.

## Why This Is Next

Authoritative Incremental ASR v1 closed the consumer side. Batch-resume now validates PCM samples,
window geometry, model and whisper.cpp binaries, prompt, language and decode options. It rejects
partial or corrupt artifacts and proves mixed cache/recompute output byte for byte against a clean
recompute.

The frozen corpus produced a split decision:

- `PROMOTE` strict interrupted-batch reuse;
- `DO_NOT_PROMOTE` live-origin reuse;
- historical checkpoint/cache process reduction: median `0.989398`, p90 `0.990849`;
- real live evidence: `0/30` required authoritative chunk proofs across three sessions.

The remaining delay is no longer an unsafe cache-reader problem. Live Shadow does not yet produce
canonical authoritative windows. Fixing that producer is more valuable now than remote diarization:
it attacks the hours-long post-stop wait while preserving the already reliable transcript path.

## Objective

Produce exact authoritative ASR work during durable capture without moving ASR into the capture
callback and without making the live draft authoritative. After stop, the existing strict consumer
must either accept each completed chunk by identity or decode it normally.

## Required Work

1. Freeze three or more real live sessions with cold post-stop timing, raw hashes and current live
   lag/resource evidence.
2. Derive canonical mic and remote ASR PCM from committed durable audio with the same preparation,
   sample boundaries, 60s hard windows and 5s overlap as batch.
3. Emit `murmurmark.authoritative_live_asr_chunk/v1` only after PCM and whisper JSON are complete and
   atomically committed. Bind the proof to the full v1 identity and output hash.
4. Keep production live preview independent. A slow canonical producer may lag, shed optional work
   or stop; it must never block capture, raw finalization or ordinary batch fallback.
5. Reconcile the final partial window after stop. Never publish a proof for an open, truncated or
   geometrically different window.
6. Run whisper.cpp with the configured low-priority work-conserving resources and expose capture,
   canonicalization, decode, lag, queue and reuse progress separately.
7. Validate accepted live-origin chunks through the existing strict materializer and
   `check-asr-chunk-cache.py --require-authoritative`. No text-similarity bridge is allowed.
8. Prove byte-identical authoritative raw ASR, clean dialogue, selected transcript, notes evidence
   and guarded export against a clean full recompute.
9. Run a frozen real corpus and make a `PROMOTE` or `DO_NOT_PROMOTE` decision. Reconcile README,
   contracts, runbooks, roadmap and OpsKarta, then commit and push.

## Acceptance Gates

- every accepted live-origin chunk has exact canonical PCM and immutable proof;
- mic and remote window indices, sample bounds and overlap policy agree with batch;
- incomplete, stale, corrupt, mismatched or missing artifacts always fall back to normal decoding;
- capture callback has no ASR, preprocessing or blocking queue work;
- raw CAF hashes and capture health remain unchanged under worker lag, crash and backpressure;
- mixed live-origin/recompute output is byte-identical to clean full recompute;
- on at least three real sessions, authoritative post-stop ASR time improves by at least `50%`
  against each session's own cold baseline;
- stable recording and ordinary batch processing regress by no more than `10%`;
- selected profile, quality verdict, local recall, chronology, protected `Me`, notes and export do
  not regress;
- any unmet hard gate produces a reproducible `DO_NOT_PROMOTE`, never a partial promotion.

## Safety Boundary

- no second capture process, raw mutation, cloud service or new primary ASR;
- no live transcript promotion and no semantic/text-similarity cache acceptance;
- no quality-threshold relaxation to gain speed;
- no Echo, Target-Me, remote diarization, speaker naming, LLM synthesis or UI changes;
- producer failure is a performance loss only.

## Definition Of Done

- canonical producer, atomic proof contract and strict consumer interoperate end to end;
- interruption, corruption, lag, final-tail and capture fail-open tests pass;
- cold/live-origin/recompute timings and SHA-256 evidence are frozen for at least three real sessions;
- corpus decision and compatibility ceiling are explicit;
- README, contracts, runbooks, roadmap and OpsKarta agree;
- all checks pass, changes are committed and pushed, worktree is clean.

## Completed Predecessor

Authoritative Incremental ASR v1 completed on 2026-08-06 with
`PROMOTE_BATCH_RESUME / DO_NOT_PROMOTE_LIVE_ORIGIN`. Evidence is frozen in
`docs/testing/2026-08-06-authoritative-incremental-asr-v1.md` and generated through
`scripts/report-authoritative-incremental-asr.py`.
