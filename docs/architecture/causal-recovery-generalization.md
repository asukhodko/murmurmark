# Causal Recovery Generalization

## Purpose

Causal recovery tries to restore local `Me` speech hidden by active `remote` speech in the live
preview. It is advisory only. Raw CAF and the batch transcript remain authoritative.

The fixed Causal Double-Talk Me Recovery v1 experiment recovered `4/16` rows and `11.56s`, but that
corpus was deliberately narrow. Generalization v1 tests the same algorithm against every eligible
remote-active source row from seven regression sessions and three independent holdout meetings.

## Evidence Boundary

Selection may use only evidence available through the current closed chunk:

- committed mic and remote PCM;
- live ASR available through that chunk;
- past-only Target-Me enrollment;
- past-only remote-dominant echo training.

Authoritative batch text and timestamps are stored under `evaluation_reference` and are read only
after the algorithm has produced its decision. Future chunks and future enrollment are forbidden.

## Generalization Corpus v1

The immutable corpus is stored under:

```text
sessions/_reports/live-pipeline/causal-recovery-generalization-v1/
```

The frozen result contains:

- `963` rows from `10` sessions;
- the original `16` positive rows without changing their source fingerprints;
- `783` eligible remote-active source rows;
- `164` offline accepted/rejected candidates;
- three holdout meetings, including a group call and two 1x1 calls, totalling `9105.631s`;
- `832` raw, Echo Guard, live, runtime and authoritative input files with SHA-256.

Every row has a stable corpus/evaluation outcome. That does not mean every eligible source row
received an expensive causal recovery decision: only `268/783` reached that stage. Evaluation
classified `284` rows as genuine double-talk, `549` as probable remote leak, `28` as probable ASR
noise, `11` as timing overlap and `91` as insufficient evidence. The corpus includes `65`
adversarial negative candidate controls. None of the negative controls was accepted.

## Promotion Decision

The decision is `DO_NOT_PROMOTE`. The profile remains explicit-only and the normal preview is
unchanged.

The blockers are independent:

1. Only `268/783` eligible source rows reached the expensive candidate stage. The remaining rows
   still have machine-readable rejection/prefilter outcomes, but candidate-stage coverage is only
   `34.2273%`.
2. All three bounded recording-time holdout replays exhausted the `28s` stage timeout. Fail-open
   worked, but final lag/equivalence gates did not pass.
3. One holdout session improved missing `Me` and token F1 but increased effective order blockers
   from `3` to `4`.

The original fixed result remains `4` rows / `11.56s`; all input hashes are unchanged; timeout,
missing-model, corrupt-cache and backpressure tests fail open. The outcome fingerprint is:

```text
550a6eb71cf3defdf2b5ed29659cfce0da2a900c846e7f50c9548f51d15fe147
```

## Candidate Prefilter Result

Causal Candidate Coverage and Cheap Negative Prefilter v1 reused this immutable corpus and completed
with a second `DO_NOT_PROMOTE` decision. The existing generalization directory was not overwritten.

The shared selection function assigns every eligible source row exactly one past-only route:

```text
eligible remote-active row
  -> cheap_reject        48
  -> expensive_candidate 159
  -> unresolved          576
```

All `783/783` rows have reasons and a deterministic fingerprint. Selection reads current/past live
ASR, committed PCM evidence and past Target-Me enrollment; it cannot read authoritative batch text,
future chunks or evaluation references. Cheap rejection removed no frozen genuine-double-talk row.

The expensive stage accepted `39/87` candidates / `191.26s`. It preserves the original `4` fixed
recoveries / `11.56s` and passes all ten per-session no-regression checks. All `65` frozen negative
controls remain rejected, but one new accepted candidate is post-hoc probable ASR noise.

Runtime routing matches offline routing for all `643` holdout rows. Final lag is zero, but none of
the three holdouts passes every gate. There are `20` fail-open expensive-stage timeouts and maximum
overall p95 is `42.634s`, above the `30s` limit. The cheap watermark is persisted before expensive
work, so a timeout is not retried at each later cutoff.

This closes the current promotion branch. Normal preview stays unchanged and batch stays
authoritative. A persistent local faster-whisper process may later test whether model reuse removes
the cold-start cost, but it must use this same corpus and cannot become product work until both
negative acceptance and runtime gates are solved. The current product focus returns to authoritative
batch order/boundary review closure.
