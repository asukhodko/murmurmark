# Current Goal

Status: recommended next

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded
production audio profile. Evidence Handoff v2 remains the only input to guarded export.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Causal Canonical Mic ASR v1

OpsKarta nearest goal: Causal Canonical Mic ASR v1: снять измеренный mic critical-path после Canonical Live ASR Producer v1: сделать Echo Guard и выбранную Speaker-Preserving mic-подготовку checkpointable на закрытых committed-PCM окнах, выпускать exact canonical 60s/5s mic ASR chunks только при полном совпадении PCM/model/prompt/decode identity с post-stop batch, сохранить raw CAF и batch authoritative, fail-open при lag или расхождении и добиться не менее 50% сокращения свежего post-stop ASR wall time на трёх реальных сессиях либо завершить воспроизводимым DO_NOT_PROMOTE.

## Why This Is Next

Canonical Live ASR Producer v1 proved strict remote parity on `3/3` frozen sessions. It also exposed
the real ceiling: mic and remote are decoded in parallel, therefore removing remote decode alone
reduces modeled wall time by only `2.8651%..4.1040%`. The same work removes about `51%` of aggregate
ASR CPU time, but it does not materially shorten the user's wait.

The remaining critical path is mic. Its authoritative input is selected only after local FIR Echo
Guard and guarded Speaker-Preserving Neural Echo policy. Live raw mic is therefore evidence, not the
canonical batch input. The next step must move or checkpoint that exact preparation boundary rather
than accepting approximate audio or provisional text.

## Objective

Produce exact authoritative mic ASR work before or immediately after stop without changing capture,
weakening Echo safety or making a live draft authoritative. Completed work is reused only through
the existing strict chunk identity; every uncertainty returns to ordinary batch.

## Required Work

1. Trace the exact mic lineage from committed PCM through Echo Guard, promotion policy,
   `mic_for_asr.wav`, export and whisper.cpp chunking.
2. Separate causal, delayed-commit and whole-session-only operations. Freeze the minimum future
   context needed before a mic window can become immutable.
3. Build an isolated checkpoint producer for all production-eligible mic branches. Selection after
   stop may choose a precomputed branch, but may not reinterpret its PCM identity.
4. Emit authoritative proof only when prepared PCM is byte-identical to post-stop canonical PCM and
   model, binary, prompt, language, decode options and output JSON hashes match.
5. Keep all preparation, copying and ASR outside the capture callback in bounded low-priority work.
   Lag, crash, queue overflow and unsupported acoustic modes disable only the optimization.
6. Reconcile the final tail and any delayed Echo context after stop; decode only missing or rejected
   windows through the ordinary batch path.
7. Verify raw ASR, clean dialogue, selected profile, notes, verdict and guarded export byte for byte
   against a clean recompute.
8. Freeze at least three fresh real sessions and decide `PROMOTE` or `DO_NOT_PROMOTE` from measured
   post-stop wall time, capture safety and transcript quality.

## Acceptance Gates

- accepted mic chunks are exact post-stop canonical PCM, never merely similar audio;
- raw capture hashes, duration, silence/sparse checks and normal live preview do not regress;
- protected local speech, chronology, remote-like `Me`, review burden and export integrity do not
  regress;
- missing enrollment, unsupported Echo profile, stale artifacts, corruption or lag always use
  batch fallback;
- ordinary production does not run unpromoted heavy work by default;
- three fresh sessions show at least `50%` lower post-stop ASR wall time, or the hypothesis closes
  with an explicit reproducible ceiling.

## Safety Boundary

- no second capture process, raw mutation, cloud service or new primary ASR;
- no approximate cache acceptance, text-similarity bridge or live transcript promotion;
- no weaker Echo/Target-Me gate to gain speed;
- no remote diarization, speaker naming, LLM synthesis or UI work in this goal.

## Completed Predecessor

Canonical Live ASR Producer v1 completed on 2026-08-06 with `DO_NOT_PROMOTE`. Exact remote parity
passed `3/3`, but modeled post-stop wall reduction was only `2.8651%..4.1040%` and fresh
recording-time coverage was absent. The producer and consumer remain quarantined behind explicit
evidence flags. See `docs/testing/2026-08-06-canonical-live-asr-producer-v1.md`.
