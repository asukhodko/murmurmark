# Current Goal Context

This file keeps the latest goal context and the most relevant completed goals. The stable
production path remains non-live `record -> process`. The current live work is evidence gathering
and gate hardening only: live output must stay shadow-only and batch transcript remains
authoritative.

## Latest Completed Goal: Experimental Sidecar Contract v1

Status, 2026-07-07: complete.

Goal:

```text
MurmurMark Experimental Sidecar Contract v1: реализовать безопасный single-capture
experimental sidecar-контур, где raw CAF остаётся единственным источником истины, а live/near-
realtime артефакты пишутся отдельно, best-effort, с машинно-проверяемым доказательством, что
эксперимент не повлиял на raw capture и batch pipeline.
```

Plainly: live work must have a passport. It may produce chunks, draft text and comparison reports,
but it must also prove where those files are, whether it fell behind, whether it disabled itself,
whether raw capture was affected, and how to recover batch processing from raw CAF without starting
a new recording.

Current implementation target:

- `derived/experiments/live-shadow-v1/experiment_manifest.json`;
- `derived/experiments/live-shadow-v1/state.json`;
- `derived/experiments/live-shadow-v1/events.jsonl`;
- `derived/experiments/live-shadow-v1/report.json`;
- `derived/experiments/live-shadow-v1/report.md`;
- `murmurmark experiment status|report|compare SESSION|latest`;
- fail-open smoke: artificial sidecar backpressure disables only sidecar artifacts while raw CAF and
  batch recovery remain valid.

Batch transcript remains authoritative. Live promotion remains blocked.

Definition of done:

- controlled live pilot writes the experiment contract;
- `state.json` answers raw seconds, sidecar seconds, backpressure/disabled state,
  `raw_capture_affected` and batch reproducibility from raw CAF;
- `murmurmark process SESSION` works from raw files even when sidecar fails;
- smoke tests cover sidecar backpressure and contract schema;
- docs/contracts/roadmap describe the experiment namespace and keep `derived/live` as compatibility.

Completion evidence:

- `murmurmark live pilot --duration 8 --segment-sec 5 --overlap-sec 1 --skip-safety-gate --out sessions/_sidecar-contract-pilot-smoke`
  wrote `derived/experiments/live-shadow-v1/experiment_manifest.json`, `state.json`,
  `events.jsonl`, `report.json` and `report.md`;
- `murmurmark experiment compare sessions/_sidecar-contract-pilot-smoke --experiment live-shadow-v1`
  refreshed comparison and contract with `raw_capture_affected: false`,
  `batch_authoritative: true`, `promotion_allowed: false`;
- `scripts/smoke-experimental-sidecar-contract.sh` covers normal and backpressure contract fixtures;
- `scripts/smoke-live-segment-fail-open.sh` verifies raw capture survives overloaded sidecar queue
  and writes fail-open contract evidence;
- `MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh` passed;
- `scripts/check.sh` passed.

## Current Goal: Near-Realtime Live Parity Coverage v1

Status, 2026-07-08: active, blocked by parity gates rather than capture loss.

Goal:

```text
Near-Realtime Live Parity Coverage v1: получить реальные live-pipeline сессии, сравнить live
chunks/drafts с batch output и держать live promotion заблокированным, пока parity gates не докажут
безопасность по order risk, local recall, remote leakage, review burden, selected notes readiness и
chunk-boundary risks. Batch transcript остаётся authoritative.
```

Plainly: live can be studied only as a shadow speed-up candidate. The user-facing product path stays
batch-first until live proves that it does not damage raw capture, preserves local speech, does not
introduce remote leakage or ordering errors, keeps review burden acceptable, produces notes-ready
batch output and does not break on chunk boundaries.

Current state:

- real live sessions in the corpus: `11`;
- diagnostic/lab live sessions kept out of promotion scope: `9`;
- real live-vs-batch compared sessions: `8`;
- meaningful real comparisons: `4`;
- real passing comparisons: `1`;
- capture-safe candidate sessions: `2`;
- capture-safe candidate passing sessions: `1`;
- capture-safe evaluable sessions: `3`;
- promotion decision: `shadow_only_do_not_promote`;
- new real live collection allowed: `false`;
- controlled real live pilot collection is allowed only as evidence collection after the full
  fail-open proof; batch remains authoritative and promotion remains blocked;
- current blocking dimensions: `capture_safety`, `order_risk`, `local_recall`, `remote_leakage`,
  `review_burden`, `selected_notes_readiness`, `chunk_boundary_risks`, `draft_text_recall`,
  `required_artifacts`.
- capture-safe candidate blocking dimensions: `order_risk`, `local_recall`, `selected_notes_readiness`.
- current objective next focus: `fix_live_local_recall_gap`.

Safety constraint:

- do not use `--live-pipeline` as the normal production recording path while live is quarantined;
- use `record --experiment live-shadow-v1` only as controlled Live Evidence, not as a production
  transcript source;
- historical unsafe live sessions remain negative evidence and must not be treated as promotion
  candidates;
- batch transcript remains authoritative and promotion remains blocked.

Definition of done:

- existing real live sessions are classified as real-vs-diagnostic in corpus reports;
- every real live session either has a live-vs-batch comparison or a precise blocker explaining why
  comparison is impossible;
- promotion dimensions are separated and machine-readable: capture safety, order risk, local
  recall, remote leakage, review burden, selected notes readiness, chunk-boundary risks, draft text
  recall and required artifacts;
- strict gates fail while any required dimension is warning/failed/blocked/not evaluated;
- `promotion_policy` keeps `batch_authoritative: true`, `live_quarantined: true`,
  `new_real_live_collection_allowed: false` and `promotion_allowed_sessions: 0`;
- after full fail-open proof, `controlled_real_live_pilot_allowed: true` is not enough to record a
  valuable meeting; the runner still blocks new real live capture without
  `--allow-unsafe-controlled-real-recording`;
- README/runbooks/contracts/roadmap make clear that live is quarantined and that corpus reports are
  evidence, not a command to collect new unsafe live meetings.

Latest verification:

```bash
murmurmark corpus live all --refresh
```

Current result:

- `status = shadow_only_not_promotable`;
- `promotion_decision = shadow_only_do_not_promote`;
- `promotion_allowed_sessions = 0`;
- live/batch comparison granularity: ASR segment when available, chunk fallback otherwise;
- `real_live_order_mismatch_count = 34`;
- `real_live_order_mismatch_by_category = {"same_chunk_same_source_reorder": 20,
  "same_chunk_cross_source_reorder": 11, "cross_chunk_reorder": 2,
  "chunk_overlap_context_reorder": 1}`;
- `real_live_order_mismatch_by_primary_risk = {"role_conflict_or_remote_leak": 19,
  "weak_text_match_possible_false_positive": 8, "same_source_timeline_reorder": 4,
  "cross_source_timeline_reorder": 3}`;
- `real_live_order_mismatch_by_confidence = {"role_conflict": 19, "low": 8, "high": 4,
  "medium": 3}`;
- `real_live_role_constrained_order_mismatch_count = 9`;
- `real_live_role_constrained_order_mismatch_by_category = {"same_chunk_same_source_reorder": 6,
  "same_chunk_cross_source_reorder": 3}`;
- `real_live_role_constrained_order_mismatch_by_confidence = {"high": 4, "medium": 5}`;
- `real_live_contentful_role_constrained_order_mismatch_count = 4`;
- `real_live_contentful_role_constrained_order_mismatch_by_category =
  {"same_chunk_same_source_reorder": 2, "same_chunk_cross_source_reorder": 2}`;
- `real_live_contentful_role_constrained_order_mismatch_by_confidence = {"high": 1,
  "medium": 3}`;
- `real_live_contentful_role_constrained_order_mismatch_by_ambiguity = {"ambiguous": 2,
  "unambiguous": 2}`;
- `real_live_unambiguous_contentful_role_constrained_order_mismatch_count = 2`;
- `real_live_missing_me_seconds = 419.16`;
- `real_live_missing_me_visible_in_suppressed_mic_seconds = 382.52`;
- `real_live_missing_me_not_visible_in_suppressed_mic_seconds = 37.21`;
- `real_live_suppressed_mic_turn_count = 30`;
- `real_live_segment_role_gate_candidate_chunk_count = 9`;
- `real_live_segment_role_gate_candidate_kept_segment_count = 54`;
- `real_live_rescue_shadow_candidate_chunk_count = 2`;
- `real_live_rescue_shadow_candidate_segment_count = 9`;
- `real_live_rescue_shadow_missing_me_recovered_seconds = 45.36`;
- `real_live_rescue_shadow_missing_me_seconds_after = 373.80`;
- `real_live_rescue_shadow_suspected_remote_leak_in_me_seconds = 0.00`;
- `real_live_rescue_shadow_order_mismatch_count = 34`;
- `real_live_suppressed_mic_asr_me_dominant_segment_count = 49 / 209.58 sec`;
- `real_live_suppressed_mic_asr_mixed_segment_count = 44 / 199.92 sec`;
- current text-only rescue policy: `152.60 sec` local / `73.62 sec` remote-risk;
- strict unique-token text policy: `143.52 sec` local / `219.12 sec` remote-risk;
- remote-silent text policy: `34.16 sec` local / `2.58 sec` remote-risk;
- audio remote-quiet policy: `51.14 sec` local / `15.42 sec` remote-risk;
- audio mic-dominant policy: `24.00 sec` local / `0.00 sec` remote-risk;
- audio low-coherence policy: `176.98 sec` local / `193.18 sec` remote-risk;
- audio safe union policy: `50.18 sec` local / `2.58 sec` remote-risk,
  `68.42 sec` missing-Me recovered;
- batch-oracle local ceiling: `409.50 sec` local;
- scoped capture-safe candidate rescue status: `no_material_live_candidate`;
- scoped capture-safe candidate best live policy: `current_text_segment_gate`
  (`1.80 sec` local / `0.00 sec` remote-risk), below the material threshold;
- scoped capture-safe candidate batch-oracle ceiling: `13.06 sec` local;
- `real_live_suspected_remote_leak_in_me_seconds = 15.96`;
- `coverage_path = resolve_capture_safe_candidate_blockers`;
- `objective_next_focus = fix_live_local_recall_gap`.
- `capture_safe_evaluable_local_recall_gap_examples = 12 / 47.44 sec`.

The report now keeps concrete missing-Me rows under
`capture_safe_evaluable_local_recall_gap_examples`. This includes capture-safe runs that are not
`meaningful_live_comparison` because the live draft lost all `Me` turns; those sessions must remain
visible for the local-recall fix.

The comparison now evaluates normal live turns and rescue-shadow candidates at ASR-segment
granularity when the source ASR JSON is present, with chunk-level fallback for older artifacts. This
exposes order and remote-leakage risks that the earlier chunk-level comparison could hide.
Order risk is mostly local to a single live chunk: `31/34` mismatches are same-chunk reorder, with
`20` inside one source and `11` between mic/remote segments. Only `3/34` are cross-chunk or overlap
context. The primary-risk split is even more useful: `19/34` are role conflict / possible remote
leak, `8/34` are weak text matches that may be false positives, and only `7/34` look like direct
timeline reorder. A stricter same-role matcher confirms `9` role-constrained order mismatches
(`6` same-source and `3` cross-source, all inside one live chunk); after filtering short/generic
phrases, only `4` contentful same-role order mismatches remain. This points the next implementation
at targeted role-constrained live reconciliation and per-chunk timeline repair. Ambiguity scoring
narrows the stable contentful order-risk subset to `2` examples, so order repair should be
targeted; raw capture and sidecar materialization are not the next bottleneck. The metric-aware
objective focus now points to `fix_live_local_recall_gap`, because stable contentful order risk is
small while `419.16s` of batch `Me` speech are still missing from live `Me`, with `382.52s` visible
in suppressed mic evidence.
Most missing `Me` seconds are still visible in `raw_text_before_role_gate` / suppressed mic chunks,
but the live branch also has segment-level ordering drift and `15.96s` suspected remote leakage in
published live `Me`. The current live blockers are therefore: live timeline ordering, remote leakage
and the coarse `live_role_gate`, which suppresses an entire mic chunk when the chunk looks like
remote duplicate even if it also contains real local speech. Segment-level batch comparison now
shows `49` Me-dominant suppressed mic ASR segments
(`209.58s`) and `44` mixed suppressed mic ASR segments (`199.92s`) in real live runs. A first
text-only segment rescue found `9` real live candidate chunks / `54` kept candidate segments, but it
stays diagnostic-only because policy-lab metrics show it would recover `152.60s` local speech while
risking `73.62s` remote leakage. A stricter unique-token text rule is not better enough:
`143.52s` local / `219.12s` remote-risk. `remote_silent_text_v1` is much safer
(`34.16s` local / `2.58s` remote-risk), but covers only a small slice of the `409.50s` batch-oracle
local ceiling. The next implementation step should therefore add audio/evidence gates, not only text
thresholds, to split or rescue local evidence inside suppressed mic chunks without publishing remote
leak. The first audio policy lab narrows that path: `audio_mic_dominant_v1` is clean but small
(`24.00s` local / `0.00s` remote-risk), `audio_low_coherence_v1` is unsafe
(`176.98s` local / `193.18s` remote-risk), and `audio_safe_union_v1` is the best current shadow
candidate (`50.18s` local / `2.58s` remote-risk, `68.42s` missing-Me recovered). Fresh live chunks
now expose that candidate separately as `live_rescue_shadow`; in the current corpus this appears in
`2` real-live chunks / `9` segments and recovers `45.36s` missing-Me in actual shadow artifacts.
The shadow itself does not add measured remote-risk (`0.00s`), but it still leaves `373.80s`
missing-Me and does not reduce the `34` segment-level order mismatches when evaluated as a combined
draft. It remains
shadow-only evidence, not a promotable live `Me` path. The scoped candidate diagnostic is stricter:
existing live-implementable policies do not recover a material amount of local speech in
promotion-candidate sessions, even though the batch oracle shows recoverable `Me` content in the
same suppressed regions.

New Target-Me diagnostic, 2026-07-08:

- `scripts/audit-live-local-recall-target-me.py` audits suppressed live mic ASR segments with the
  local Target-Me speaker evidence backend;
- current corpus run: `6` sessions, `77` audited items / `334.12s`;
- `4` sessions had enough Target-Me enrollment, `2` reported `insufficient_enrollment`;
- audited local/mixed seconds: `295.34s`;
- Target-Me confirmed local seconds: `142.34s`;
- Target-Me possible or confirmed local seconds: `287.98s`;
- remote-risk false-positive seconds in the audited set: `38.78s`;
- Target-Me rejected remote-risk seconds: `0.00s`.
- recommended shadow policy: `target_me_confirmed_remote_guard_v1`;
- `target_me_confirmed_remote_guard_v1` selected `118.54s`, covered `116.10s` local speech,
  introduced `2.44s` remote-risk, and would recover `94.68s` current missing-Me by interval overlap;
- broader `target_me_possible_v1` would recover `247.24s`, but with `37.82s` remote-risk, so it is
  too unsafe for promotion.
- `compare-live-batch.py` now evaluates these Target-Me rows as actual counterfactual live-shadow
  turns. In the real live corpus, `target_me_confirmed_remote_guard_v1` selects `118.54s`, recovers
  `128.85s` missing-Me and adds `0.00s` measured remote leak, but also adds `3` contentful
  role-constrained order mismatches. `target_me_confirmed_v1` recovers `169.24s` but adds `12.40s`
  remote leak; `target_me_possible_v1` recovers `278.15s` but adds `16.30s` remote leak and `5`
  contentful order mismatches. Therefore there is no safe Target-Me shadow policy yet.

Interpretation: more new recordings are not needed to unblock the next implementation step. The
existing corpus already contains enough suppressed live mic material and enough Target-Me evidence.
The next work is not just stronger filtering; it is timeline-safe Target-Me rescue/reconciliation:
recover local speech only where local-speaker evidence is strong enough, remote-risk evidence is low,
and inserted `Me` turns do not create new ordering errors. Sessions with `insufficient_enrollment`
point to a later calibration problem, not to a need for more ad-hoc live meetings now.

## Latest Completed Goal: Current Pipeline Stabilization v1

Status, 2026-07-06: complete.

Current baseline:

- [2026-07-06 Current Pipeline Stabilization Baseline](../testing/2026-07-06-current-pipeline-stabilization-baseline.md)

Goal:

```text
Полная стабилизация текущего MurmurMark pipeline без новых продуктовых доработок. Текущая версия
должна надёжно выполнять основной пользовательский сценарий: записал созвон -> получил понятный
итог, то есть транскрибацию/заметки или явный отказ с причиной. На время этой цели не добавлять
новые модели, repair/synthesis/audit-слои, live promotion, Echo Guard experiments или UI.
```

Plainly: MurmurMark must become boring before it becomes smarter. The supported path is:

```bash
murmurmark record --target-bundle system
murmurmark process latest
murmurmark next latest
murmurmark status latest
murmurmark finish latest
```

Current scope:

- keep `--live-pipeline` disabled by default and out of the normal meeting command until its segment
  writer no longer interferes with raw ScreenCaptureKit capture;
- prove normal capture without live writes usable mic/remote evidence or blocks early;
- block silent, partial and interrupted captures before ASR unless `--allow-partial` is explicit;
- make repeated `process` runs resume or explain the next command without stale readiness/outcome;
- make `status` and `next` agree for successful, review-first, blocked and failed-capture sessions;
- keep existing quality/review/export layers stable, but do not improve them during this goal;
- classify the current real-session corpus as usable, review_first, blocked or broken_capture;
- update README, runbooks, roadmap and opskarta around this supported route.

Definition of done:

- working tree is clean, committed and pushed;
- `caffeinate -dimsu scripts/check.sh` passes from an awake desktop session;
- live/capture regression smoke passes, but live remains diagnostic;
- at least one fresh short non-live recording with audible content produces a non-empty transcript;
- at least one fresh failed/silent capture fixture blocks before ASR;
- `murmurmark status latest` and `murmurmark next latest` give one non-conflicting next step;
- README and runbooks describe the supported production path without stale live-first guidance.
- `doctor --strict` blocks recording when ScreenCaptureKit sees no shareable display, including the
  case where macOS permissions exist but the display/session is asleep.

Explicit non-goals:

- full echo removal, new ASR models, diarization, new synthesis, new review profiles, Core Audio
  Process Tap migration, UI App or any quality tuning beyond stabilizing the existing pipeline.

## Latest Completed Goal: Reliable Processing UX v1

Status, 2026-07-02: complete.

Goal:

```text
Reliable Processing UX v1: сделать post-recording путь MurmurMark надёжным и понятным для
ежедневного использования. После записи пользователь запускает `murmurmark process latest` и
получает не поток догадок, а ясный результат: `ready_for_notes`, `review_first` или `blocked`,
путь к transcript/notes, точный review burden, причину блокировки экспорта и одну следующую
команду. Пайплайн должен нормально возобновляться после прерывания, показывать понятный прогресс
долгого ASR, не пересчитывать лишнее без `--force` и не оставлять пользователя гадать, завис процесс
или работает.
```

Plainly: MurmurMark should feel less like a lab and more like a tool. The main user question after
recording is simple: "Can I use this, where is the transcript, and what is the one next safe action?"

Why this is the right next goal:

- the core transcription/review pipeline is pilot-ready, but the terminal handoff is still too noisy;
- long ASR and judge stages can look like a hang unless progress explains what is happening;
- after interruption, the user needs a resume path, not a pile of derived files;
- `outcome.json` already exists, so the shortest path is to make it the stable UX contract.

Definition of done:

- `murmurmark outcome SESSION` prints a compact human summary: can read notes, can export, review
  burden, first review lane, next command and key file paths;
- `derived/outcome/outcome.json` contains the same compact `summary` for tools and agents;
- long-running `murmurmark process` steps print useful heartbeats with step reason, checkpoints and
  resume command;
- rerunning `murmurmark process SESSION` remains the normal recovery path after interruption;
- docs describe the simple post-recording route and where deeper diagnostics live;
- no change to capture, default `local_fir` or main `whisper.cpp` ASR.

Follow-up hardening:

- add ASR window/chunk-level resume instead of whole-stage rerun;
- add duration estimates from historical corpus timings;
- make `status` default to the compact outcome view, with diagnostics behind an explicit flag;
- keep reducing mandatory review burden at the audio source.

Completion evidence, 2026-07-02:

- `murmurmark outcome SESSION` prints `can_read_notes`, `can_export`, `export_blockers`, review
  burden, first review lane, key file paths and the next command;
- `outcome.json` carries the same compact `summary`;
- long `murmurmark process` stages print heartbeat lines with step reason, checkpoint count and
  resume command;
- `process --plan-only` writes `pipeline_plan_report.json` and does not overwrite the last real
  `pipeline_run_report.json` or refresh `outcome`;
- `Ctrl-C` during `process` terminates the current child step, writes `status: interrupted`,
  refreshes `outcome` and points back to `murmurmark process SESSION`;
- checks passed: `py_compile`, `git diff --check`, `swift build`, `scripts/smoke-cli-handoff.sh`,
  `scripts/smoke-fixture.sh`, `scripts/check-corpus-gates.py --no-fail`.

## Latest Completed Goal: Chunked/Resumable Processing v1

Status, 2026-07-03: complete.

Goal:

```text
Chunked/Resumable Processing v1: превратить тяжёлую post-recording обработку MurmurMark из
монолитных стадий в кусочный, кэшируемый и безопасно возобновляемый pipeline. После прерывания или
сбоя `murmurmark process SESSION` должен продолжать с последнего проверенного chunk/checkpoint, не
пересчитывать весь ASR без необходимости, показывать реальный прогресс по секундам/окнам и
сохранять batch-quality результат. Live/near-realtime chunks могут использоваться как кэш только
через строгие parity gates; batch transcript остаётся authoritative.
```

Why this was the right significant goal:

- Reliable Processing UX now explains what happens, but the expensive ASR stage can still rerun too
  much work;
- near-realtime already exists as a shadow branch, but its chunks are not yet trusted as batch-grade
  cache;
- true resumability reduces waiting time, makes interruptions cheap and gives a concrete path from
  batch-first to near-realtime without weakening transcript quality;
- this goal does not require changing capture, default Echo Guard or main `whisper.cpp` ASR.

Definition of done:

- ASR/cache artifacts are indexed by stable chunk identity: source audio, model, language, prompt,
  audio prep, window boundaries and tool version;
- `murmurmark process SESSION` can skip already valid ASR chunks and resume after interruption;
- run reports show chunk totals, completed seconds, missing chunks, reused chunks and estimated
  remaining heavy work;
- live/near-realtime ASR cache is reused only when strict metadata compatibility and parity checks
  pass; otherwise the batch path recomputes safely;
- corpus gates compare chunked/resumed output with current batch baseline for order risk, local
  recall, remote leakage, review burden and selected notes readiness;
- docs describe when to use `--force-asr`, when cache is reused, and how to inspect a stuck chunk.

Completion evidence:

- default `windowed` whisper.cpp ASR writes per-window chunk cache metadata and
  `raw/chunks/<track>/chunk_cache_report.json`;
- if the combined raw ASR JSON is missing but compatible chunk JSON remains, the bridge rebuilds the
  combined raw JSON from chunks instead of rerunning all windows;
- chunk identity includes source audio fingerprint, model, language, prompt, audio prep, window
  boundaries and tool version;
- `murmurmark process` heartbeat reads chunk cache reports and can show completed/missing/reused/
  transcribed ASR windows, completed/remaining audio seconds and a current-run ETA when enough
  progress exists;
- `murmurmark process` runs `check-asr-chunk-cache.py --require-chunks` after `transcribe_current`;
  `raw/chunk_rebuild_check.json` is a hard gate proving current raw ASR JSON can be rebuilt from
  cached chunks;
- `report-asr-chunk-cache-corpus.py` aggregates chunk rebuild checks across sessions, and
  `check-corpus-gates.py` now treats failed ASR chunk-cache corpus reports as hard failures;
- `materialize-live-asr-cache.py` now writes materialized `raw/chunks/<track>/` reports from eligible
  live ASR chunks, so live-cache reuse is also covered by the same rebuild gate;
- verified on a temporary 4.5s two-track session: after deleting only top-level `mic.json` and
  `remote.json`, the second run reused `6/6` chunks, rebuilt the combined raw JSON, and the rebuild
  verifier passed.
- verified on a synthetic live-cache session: live chunks were materialized into raw chunk reports,
  and `check-asr-chunk-cache.py --require-chunks` rebuilt `mic` and `remote` with `2/2` chunks.
- verified on fourteen real sessions, including a real interrupted 29-minute `murmurmark process`
  run: `report-asr-chunk-cache-corpus.py --refresh` reports `passed: 14`, `failed: 0`,
  `coverage_ratio: 0.28`, `chunks_completed: 146/146`;
- `check-corpus-gates.py --no-fail` now reports `asr_chunk_cache_status: passed` with
  `asr_chunk_cache_passed_sessions: 14` and `asr_chunk_cache_coverage_ratio: 0.28`;
- `check-corpus-gates.py --no-fail` still reports live-cache parity state, with promotion blocked.
  Diagnostic live recordings showed that inline segment writing can starve raw ScreenCaptureKit
  audio delivery. The async bounded segment queue now has full fail-open proof, but the target
  coverage gate remains red until enough controlled Live Evidence live-vs-batch runs pass;
- ASR chunk-cache corpus report now separates the next coverage work: `22` sessions have old raw
  ASR without chunk reports, and `14` sessions have no raw ASR;
- legacy top-level raw ASR caches without `raw/chunks/<track>/chunk_cache_report.json` no longer
  satisfy `windowed` cache reuse by themselves; `murmurmark process` now rebuilds per-window chunks
  first, then requires `check-asr-chunk-cache.py --require-chunks` to pass;
- `pipeline_run_report.json` now carries `progress.asr_chunks.remaining_sec`,
  `progress.asr_chunks.completed_ratio` and `progress.asr_remaining_estimate`;
- `scripts/smoke-cli-handoff.sh` now includes a controlled fake-whisper failure/resume case: the
  first run fails after one cached mic chunk, the second run reuses that chunk, transcribes the
  remaining windows and passes `check-asr-chunk-cache.py --require-chunks`.
- `scripts/smoke-process-chunk-resume.sh` now proves the same recovery through the actual
  `run-session-pipeline.py` process path: first run fails in `transcribe_current`, rerun resumes,
  reuses a cached mic chunk, passes `check_asr_chunk_cache` and writes zero remaining ASR chunks.
- the same smoke now proves controlled `Ctrl-C` behavior in the actual process path: the interrupted
  run writes `pipeline_run_report.json` with `status: interrupted`, leaves one verified mic chunk in
  cache, and the next run reuses it instead of starting ASR from zero.
- the same smoke also covers legacy raw-cache rollout: a pre-chunk `raw/mic.json`/`raw/remote.json`
  pair without chunk reports is rebuilt into per-window chunks instead of being accepted as complete.
- verified with the user-facing CLI on `sessions/2026-07-02_13-33-49`: the first real interrupted
  long run wrote `pipeline_run_report.json` with `status: interrupted`, `transcribe_current`, ASR
  progress `10/30` mic chunks and a `murmurmark process SESSION` resume command; the next run reused
  `14` cached mic chunks, finished `60/60` total chunks and passed `check_asr_chunk_cache`.

Follow-up hardening:

- broader corpus no-regression comparison for chunked rebuild vs existing batch baseline;
- corpus parity coverage for live-cache reuse is paused. Do not collect more real
  `record --live-pipeline` sessions until the live segment writer is redesigned and no longer runs
  risky work on the ScreenCaptureKit callback path.

Primary design document:

- [Reliable Transcription Route](reliable-transcription-route.md)

Consultation prompt:

- [Reliable Transcription Route / Consultation Prompt](reliable-transcription-route.md#consultation-prompt)

## Completed Base Goal: Reliable Transcription Route v1

Status, 2026-07-02: completed as v1. Outcome Contract v1, Gate Evaluator, Review Plan,
Next Command, session-level Run Manifest and outcome-aware guarded export are implemented.
Review Burden Reduction v1 is also complete: the operational corpus now has a short explicit manual
tail and no pending safe suggestions.

Completion evidence:

- every processed session writes `derived/outcome/outcome.json`, `outcome.md`,
  `review_plan.json` and `next_command.txt`;
- `murmurmark status`, `next`, `outcome`, `finish`, `report session` and `report corpus --refresh`
  expose or refresh the same outcome next action;
- interrupted processing can be resumed at session-step/output-checkpoint level;
- corpus reports separate `ready_for_notes`, `review_first`, `blocked`, partial and diagnostic
  sessions without hidden exceptions;
- exported bundles are blocked unless the chosen outcome allows export;
- `local_fir` remains the default Echo Guard path.

## Latest Completed Follow-up: Review Burden Reduction v1

Status, 2026-07-02: complete.

Goal:

```text
Review Burden Reduction v1: используя уже существующие outcome/review_plan/audio-review/
stronger-judge/Target-Me evidence, уменьшить обязательную review-очередь на реальных сессиях без
смены capture, default local_fir и основного whisper.cpp ASR. Сделать review lanes измеримыми,
безопасно автозакрывать только доказанные строки, калибровать thresholds на корпусе и добиться,
чтобы больше сессий переходили из review_first в ready_for_notes либо имели короткий, объяснимый
manual tail.
```

Completion evidence:

- operational corpus: `24` working sessions;
- readiness split: `15/24 ready_for_notes`, `9/24 review_first`,
  `0/24 do_not_use_without_manual_review`;
- mandatory review queue: `9` actions / `12` rows / `25.31s`;
- low-materiality rows excluded from mandatory review: `28` rows / `70.95s`;
- corpus gates: `passed_with_warnings`, failed gates `0`;
- calibrated hard limits: `15` review actions / `25` queue rows;
- irreducible review gate: passed with limits `15` actions / `60s`;
- pending safe suggestions: `0`.

What changed:

- `manual_tail_explanation` groups the remaining queue by reason, row count, lane, label and seconds;
- stronger-audio-judge can close a narrow timing/double-talk overlap as safe `keep_me` when local
  evidence is strong and remote/leak evidence is weak;
- suggested review now leaves ambiguous rows open instead of turning uncertainty into transcript
  edits;
- corpus gates now treat review action count as a product metric, not as a loose diagnostic;
- low-materiality single-word tails and short exact partial duplicates no longer inflate the
  mandatory review queue.

Verification:

- `py_compile` over the changed Python entry points;
- `git diff --check`;
- `scripts/check-corpus-gates.py --no-fail`;
- `scripts/smoke-fixture.sh`;
- `swift build`;
- `scripts/smoke-cli-handoff.sh`.

The goal is complete because all remaining review rows are either weak/conflicting audio evidence,
ambiguous chronology, possible unique `Me` content inside remote leak, or local-recall evidence that
is not strong enough for an automatic decision. Those rows are explicit manual tail, not hidden
automation debt.

## Latest Completed Goal: ASR-Positive Echo Candidate Hardening v1

Status, 2026-07-01: implemented as an explicit shadow/experimental profile. Target-Me Evidence
Hardening v1 is complete and pushed in `a6e9f48`: the mandatory operational review queue fell from
`7` actions / `11.19s` to `5` actions / `8.78s`, and corpus gate passes as `passed_with_warnings`.

Recommended follow-up inside the new reliability route: **ASR-positive Echo promotion readiness**.
Keep `local_fir` as default, keep `coverage_v2_remote_gate_local_fir` shadow-only, widen validation
beyond the current six-session candidate corpus, and define the future default-promotion bar with
rollback and inspection rules.

Goal:

```text
ASR-Positive Echo Candidate Hardening v1: превратить `coverage_v2_remote_gate_local_fir`
из успешного shadow audio candidate в строго проверяемый экспериментальный Echo Guard профиль,
который может быть запущен на всём рабочем корпусе, снижает ASR-visible remote leakage, не
ухудшает local recall, не меняет raw CAF и не становится default без corpus gates.
```

Plainly: keep `local_fir` as the default, but make the first ASR-positive stronger mic candidate
usable as a repeatable, measurable and reviewable profile. The goal is not “perfect echo removal in
one step”. The goal is a safe promotion path for a candidate that now improves `5/6` evaluated
sessions without local-recall regression.

Current corpus snapshot after Review Burden Reduction follow-up on 2026-07-02:

- working sessions in scope: `24`;
- diagnostic sessions excluded from readiness: `26`;
- operational verdict: `pilot_ready_with_review`;
- `ready_for_notes`: `15`;
- `review_first`: `9`;
- `do_not_use_without_manual_review`: `0`;
- notes review burden: `1.32 min`;
- transcript/export review burden: `4.09 min`;
- mandatory review queue: `9` actions / `12` rows;
- low-materiality rows outside mandatory review: `28` rows / `70.95s`;
- corpus gate review limits: `15` actions / `25` rows;
- irreducible review gate: `irreducible_manual_review_queue_present`;
- pending safe suggestions: `0`.

Why this goal mattered:

- CLI, review, export and corpus gates are already usable enough for pilot work;
- Target-Me hardening closed only the rows that could be closed safely and left the truly ambiguous
  queue explicit;
- stronger-audio-judge closed one `check_transcript_order` overlap as `keep_me` only after
  mic-clean text strongly matched `Me` and remote text stayed isolated on the remote track;
- `manual_tail_explanation` now explains the remaining queue by reason, row count and seconds;
- single-word `так` tails without action/decision/risk markers are now counted as low-materiality
  instead of mandatory review;
- short exact partial remote duplicates with no unique `Me` content are also kept out of the
  mandatory queue;
- `coverage_v2_remote_gate_local_fir` is the first real audio candidate with ASR-positive evidence:
  it has now reached `5/6` safe improved evaluated sessions and `0/6` local-recall regressions;
- if stronger mic audio becomes safer, less work is pushed into transcript repair, Target-Me review
  and remote-duplicate cleanup.

Target-Me Evidence Hardening v1 completion notes:

- audio-review packs now include open readiness review-plan rows, so Target-Me sees the same rows as
  the mandatory review queue;
- Target-Me rows carry `source_audit_ids`, and review-lane suggestions match them by
  `source_audit_id` plus interval overlap;
- local-recall rows can now be answered as `drop_me` by a reviewer or by a future high-confidence
  safe suggestion; transcript-order rows remain keep/review/skip only;
- two safe `keep_me` suggestions were applied: one lost-Me/local-recall row and one order-risk row;
- no automatic transcript edit was made outside the existing review/apply flow.

Remaining review queue after the completed Target-Me pass:

- `sessions/2026-06-30_11-15-56`: one local-recall check, `1.08s`;
- `sessions/2026-07-01_14-01-09`: one local-recall check, `0.92s`;
- `sessions/2026-07-01_11-17-22`: two lost-Me checks, `2.72s`;
- `sessions/2026-06-30_17-17-20`: one uncertain audio check, `4.06s`.

This queue is intentionally not auto-closed. It is the current irreducible manual review list for
medium-risk use.

Important scope note: `ready_for_notes` is not the same as unattended full-transcript export. The
current corpus still has transcript/export review surface, and `finish` must block export when
export blockers remain. That is acceptable for the practical v2 state only if `status`, `review
progress`, `finish`, export and corpus reports point to the same residual review work.

Implementation result:

- `murmurmark audit asr-positive-echo-candidate SESSION` writes
  `derived/preprocess/echo/asr_positive_echo_candidate_report.{json,md}`;
- `murmurmark corpus echo-candidate SESSION...` writes
  `sessions/_reports/asr-positive-echo-candidate/asr_positive_echo_candidate_corpus_report.{json,md}`;
- current six-session corpus: `5/6` safe improved, `1/6` not applicable, `0/6` local-recall
  regressions;
- `murmurmark corpus gate` reads the report and enforces `shadow_only_do_not_promote`;
- candidate artifacts stay separate from `mic_for_asr.wav`, raw CAF and default `local_fir`.

Remaining work after this goal:

- widen the corpus before any promotion discussion;
- define rollback and inspection criteria for a non-default promoted bundle;
- keep comparing review burden, Target-Me evidence and remote-forbidden evidence;
- do not change default Echo Guard until a separate promotion-readiness goal passes.

# Recently Completed Goal: Review-Loop Stabilization v1

Status, 2026-07-01: completed and pushed in `bb8317b`.

The goal is to make the CLI path boring and reproducible after a session has been recorded:

```text
record -> process -> review suggested apply -> status/report
```

The important promise is not “zero review”. The promise is that every layer reports the same
remaining review queue, automatic decisions are cumulative, and unresolved seconds stay explicit.

Current implementation:

- `apply-review-workspace-decisions.py` preserves already reviewed rows even when the current
  generated template no longer contains them.
- `suggested_closure` now computes newly closed rows by stable review-row keys, not by list position.
- `report-session-quality.py` uses `review_decisions_progress.remaining_seconds` directly instead of
  reconstructing seconds from rounded minutes.
- `murmurmark review suggested` rebuilds lane packs, refreshes suggestions from cached
  stronger-audio-judge and Target-Me evidence, applies only safe generated answers, and then refreshes
  `reviewed_v1` readiness when anything was closed.
- `murmurmark process` now gives the stronger-audio-judge stage an `80` item budget by default. The
  older `12` item cap was too low for long meetings: on `sessions/2026-07-02_16-01-27`, a broader run
  reduced the manual tail from `78.52s` to `11.19s`, and a later timing-overlap evidence rule reduced
  it again to `8.92s`; on `sessions/2026-07-02_17-32-08`, it reduced
  the tail to `5.03s`.
- `audit-stronger-audio-judge.py` now treats a narrow `probable_timing_overlap` class as safe
  `keep_me` evidence when group-overlap already shows strong local support and weak remote/leak
  support. This closes benign timestamp overlaps without deleting text; conflicting double-talk
  remains manual.
- Targeted stronger-audio-judge is cached-first in the normal suggested path. It does not start a
  long new faster-whisper decode unless explicitly enabled:

```bash
MURMURMARK_TARGETED_JUDGE_COMPUTE=1 \
MURMURMARK_TARGETED_JUDGE_MAX_COMPUTED=4 \
murmurmark review suggested apply "$SESSION"
```

- Target-Me evidence is consumed by lane suggestions as high-confidence `keep_me` support when rows
  already exist. Refreshing Target-Me inside suggested review is explicit because it can be slower
  than the handoff itself:

```bash
MURMURMARK_REVIEW_TARGET_ME_REFRESH=1 murmurmark review suggested apply "$SESSION"
```

Verification snapshot:

- `sessions/2026-07-01_11-17-22`:
  - `review progress`: `2` remaining rows / `2.72s`;
  - workspace `suggested_closure`: `2` remaining rows / `2.72s`;
  - session-quality `review_scope_remaining_seconds`: `2.72s`.
- `sessions/2026-07-01_14-01-09`:
  - `review progress`: `7` remaining rows / `17.3s`;
  - workspace `suggested_closure`: `7` remaining rows / `17.3s`;
  - session-quality `review_scope_remaining_seconds`: `17.3s`.

# Recent Quality Goal: Target-Me Extraction Spike v1

Status, 2026-07-01: in progress as a shadow-only evidence spike. Not promoted.

The goal is to test whether MurmurMark can distinguish the user's real microphone speech from
remote leakage, double-talk and open-space background speech without changing capture, `local_fir`,
the main `whisper.cpp` ASR path or selected production profiles.

The implemented local baseline layer now has two MFCC variants:

- collect enrollment material from high-confidence local-only `Me` utterances;
- `mfcc_voiceprint_v0`: compute a simple local acoustic voiceprint with MFCC/spectral/pitch
  features;
- `mfcc_contrastive_v0`: add clean remote speech as a negative class and score risky clips against
  both `Me` and remote centroids;
- write `derived/audit/target-me/target_me_enrollment.json`;
- write `derived/audit/target-me/target_me_audit.jsonl`;
- write `derived/audit/target-me/target_me_summary.json`;
- write `derived/audit/target-me/target_me_report.md`;
- write corpus output under `sessions/_reports/target-me/`.

`--method auto` uses local WavLM if model files are present, otherwise `resemblyzer_dvector_v0` if
the package is installed, otherwise `mfcc_contrastive_v0`.

The first real local speaker-embedding backend has also been tested:

- `resemblyzer_dvector_v0` uses local `resemblyzer.VoiceEncoder`;
- install command: `.venv/bin/pip install resemblyzer`;
- explicit command: `murmurmark audit target-me "$SESSION" --method resemblyzer_dvector`;
- it writes the same shadow artifacts and does not edit transcripts or cleanup profiles.

The WavLM speaker-embedding backend is wired but not evaluated yet:

- `wavlm_xvector_v0` uses local `transformers` `AutoModelForAudioXVector`;
- default model path: `~/.local/share/murmurmark/models/target-me/wavlm-base-plus-sv`;
- explicit command: `murmurmark audit target-me "$SESSION" --method wavlm_xvector`;
- when model files are missing, the audit writes `status: missing_embedding_model`.

Current local backend probe:

- `torch`: available;
- `transformers`: available;
- `faster_whisper`: available, but it is an ASR judge, not speaker identity;
- `resemblyzer`: available after local `.venv` install;
- `speechbrain`, `pyannote`, `asteroid`, `wespeaker`: not installed;
- local WavLM model files: missing;
- ready source-separation candidate: none.

This keeps the spike local-only and reproducible. No audio is sent to an external service.

Current six-session smoke:

- ready enrollment sessions: `6/6`;
- audited clips: `102`;
- audited seconds: `479.4`;
- method: `mfcc_contrastive_v0`;
- `target_me_possible`: `89` clips / `441.89s`;
- `target_me_ambiguous`: `13` clips / `37.51s`;
- new review-burden reduction: `0` clips / `0.0s`;
- corroborating existing reliable evidence: `0` clips / `0.0s`;
- research decision: `no_clear_gain_yet_keep_as_evidence`;
- promotion decision: `shadow_only_do_not_promote`.

Current six-session d-vector smoke:

- method: `resemblyzer_dvector_v0`;
- ready enrollment sessions: `6/6`;
- audited clips: `102`;
- `target_me_confirmed`: `67` clips / `355.77s`;
- `target_me_possible`: `31` clips / `103.67s`;
- `target_me_ambiguous`: `4` clips / `19.96s`;
- new keep-evidence rows: `13` clips / `48.82s`;
- corroborating existing evidence: `54` clips / `306.95s`;
- readiness impact: `shadow_only_not_applied`; actual `ready_for_notes` / `review_first` / `risky`
  counts do not change until this evidence is integrated into review decisions;
- research decision: `promising_shadow_evidence_continue`;
- promotion decision: `shadow_only_do_not_promote`.

Interpretation: cheap acoustic voiceprints are useful for instrumentation, but they are not strong
enough to solve Target-Me extraction. Local d-vector embeddings are promising as a review/evidence
layer: they can protect real `Me` utterances that older audits marked as `remote_duplicate` or
`uncertain`. The current environment still has no ready source-separation package, and d-vector
evidence must stay behind corpus gates before it can drive automatic review decisions.

Working commands:

```bash
.venv/bin/pip install resemblyzer
murmurmark audit target-me "$SESSION" --profile auto --max-items 80
less "$SESSION/derived/audit/target-me/target_me_report.md"
```

```bash
.venv/bin/python scripts/audit-target-me.py \
  sessions/2026-06-23_14-04-37 \
  sessions/2026-06-25_11-14-27 \
  sessions/2026-06-26_11-15-50 \
  sessions/2026-06-26_12-04-04 \
  sessions/2026-06-29_16-31-02 \
  sessions/2026-06-30_17-17-20 \
  --profile auto \
  --max-items 20
```

# Latest Completed Goal: ASR-Positive Audio Candidate v2

Status, 2026-07-01: completed as a shadow-only Echo Guard candidate. Not promoted.

Remote-Forbidden Evidence Coverage v2 made the audit layer wide enough to judge real suspicious
windows instead of only a few early `remote_only` clips. ASR-positive audio candidate v2 uses that
judge and adds a real audio candidate: `coverage_v2_remote_gate_local_fir`.

The candidate remains shadow-only. `local_fir` is still the production default.

## Goal

Build a shadow Echo Guard candidate that reduces recognizable remote words in the `Me` path better
than current `local_fir`, without losing local-only words.

In plain words: use the Coverage v2 ASR windows as a judge, run audio-side cleanup candidates
through the same token-level remote-forbidden audit, and keep only candidates that improve remote
leakage without making the user's real speech worse.

## Why This Goal Now

Post-ASR cleanup, review lanes and remote-forbidden transcript guards are useful, but they are
compensation layers. The root problem remains that remote speech can enter the mic track and become
recognizable as `Me`.

Coverage v2 changed the situation:

- it selects suspicious ASR windows from speaker state, audio-review, stronger audio judge,
  group-overlap, transcript-overlap and local/order risk artifacts;
- the six-session smoke improved from `1/6` to `4/6` safe ASR-visible cases;
- local-only word recall did not regress;
- every selected window has an explainable `selection_reason`.

That measurement was enough to run a real candidate search without relying on loudness or ERLE
proxies.

## Scope

In scope:

- add one or more shadow audio candidates under `derived/preprocess/audio/`;
- evaluate every candidate with Coverage v2 ASR windows;
- compare candidates against `local_fir`, not against raw mic only;
- report remote-token leakage before/after, local-word recall, guarded seconds and review burden;
- keep candidate artifacts separate from `mic_for_asr.wav`;
- explain why each candidate wins, loses or remains inconclusive.

Candidate families worth trying first:

- smarter segment switching between `local_fir`, `remote_floor` and raw mic;
- stricter remote-only masking only where speaker state and ASR evidence agree;
- adaptive residual masking driven by `echo_hat`, remote energy and token guard outcome;
- per-window candidate selection: different cleanup strength for remote-only, boundary and
  double-talk windows.

Implemented v2 candidate:

- starts from the safer local-fir/segment-switch path;
- reads Coverage v2 ASR windows and their `selection_reason`;
- applies `remote_floor` cleanup only where the window is remote-risky and speaker-state does not
  show strong local speech;
- writes `offline_aec_v2_coverage_gate_plan.jsonl`;
- appears in ASR audit/report fields as `coverage_v2_remote_gate_local_fir`.

Out of scope:

- replacing `local_fir` as default;
- changing capture, raw CAF tracks, main `whisper.cpp` ASR or selected transcript profiles;
- adding cloud models;
- silently deleting uncertain `Me` text;
- claiming waveform-perfect echo removal;
- training a neural suppressor before the deterministic candidate search is exhausted.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged: passed.
- `local_fir` remains default: passed.
- At least one shadow candidate reduces ASR-visible remote-token leakage versus `local_fir` on the
  six-session smoke corpus: passed.
- Local-only word recall does not degrade by more than 2 percentage points on any evaluated
  session: passed.
- Corpus report explains every non-improved session as one of:
  `no_baseline_asr_visible_leak`, `candidate_not_better`, `local_recall_risk`,
  `asr_audit_inconclusive` or `not_enough_evidence`: passed.
- `murmurmark audit remote-forbidden` and corpus reports show candidate comparison clearly: passed.
- No audio or transcript candidate is promoted to default: passed.

## Working Commands

Baseline evidence audit:

```bash
murmurmark audit remote-forbidden "$SESSION" --profile auto
```

Current lab command for candidate evaluation:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" \
  --asr-audit \
  --asr-window-profile coverage_v2 \
  --asr-max-clips 2 \
  --asr-max-risk-clips 2 \
  --asr-max-local-clips 1 \
  --asr-candidate-keys coverage_v2_remote_gate_local_fir
```

Corpus summary:

```bash
.venv/bin/python scripts/report-offline-aec-v2-corpus.py SESSION...
.venv/bin/python scripts/report-remote-forbidden-corpus.py SESSION...
```

Expected user-facing shape after this goal:

```bash
murmurmark audit remote-forbidden "$SESSION" --profile auto
murmurmark report "$SESSION"
less "$SESSION/derived/audit/remote-forbidden/remote_forbidden_review.md"
```

The result is shadow-only, but the report now says whether an audio candidate is actually better
than `local_fir`.

## Current Finding

Six-session smoke after ASR-positive audio candidate v2:

- `reports_found = 6/6`;
- `asr_audio_candidate_gate_passed = 4`;
- `asr_audio_candidate_safe_improved = 4`;
- `asr_local_word_recall_regressions = 0`;
- assessment classes:
  - `safe_improved = 4`;
  - `no_baseline_asr_visible_leak = 2`;
- `promotion_decision = do_not_promote_from_v0_corpus_report`.

Interpretation: the goal is achieved as a shadow candidate. The candidate is useful evidence and a
stronger research baseline, but it is not yet a production replacement for `local_fir`.

## Historical Larger Goals After This Layer

These were the next options when ASR-positive audio candidate v2 had just landed:

1. Target-Me Evidence Hardening v1: integrate `resemblyzer_dvector_v0` into review suggestions
   behind corpus gates, without automatic transcript edits.
2. Neural residual echo suppression spike: test a narrow local model only after deterministic
   candidate reports prove where it is needed.
3. Token-level remote-forbidden transcript reconciliation: keep a final safety net even when audio
   cleanup improves.

## Latest Completed Goal: Remote-Forbidden Evidence Coverage v2

Status, 2026-07-01: completed as a shadow/review evidence coverage layer. Not promoted.

Coverage v2 fixed the main v1 blocker. Instead of auditing only the first small set of
speaker-state clips, it selects risky ASR windows from speaker state, audio-review rows, stronger
audio judge rows, group overlaps, transcript overlaps and local/order risk artifacts.

Six-session smoke after Coverage v2:

- `reports_found = 6/6`;
- `safe_improved_sessions = 4`;
- `local_recall_regressions = 0`;
- `asr_windows_evaluable = 24`;
- `asr_windows_skipped = 578`;
- `suggest_drop_count = 1`;
- `quarantine_count = 16`;
- `needs_review_count = 1`;
- `target_status = target_met_two_sessions`.

Interpretation: Coverage v2 meets the evidence target. It does not promote audio or transcript
candidates. Its main value is that the next audio-candidate search now has a real ASR-visible judge.

## References

- [Complete Echo Removal Research](../research/2026-06-30-complete-echo-removal.md)
- [Echo Guard architecture](../architecture/echo-suppression.md)
- [Mic remote bleed reduction backlog](../backlog/mic-remote-bleed-reduction.md)
- [CLI roadmap](../roadmap/murmurmark-cli-roadmap.md)

---

# Latest Completed Goal: Remote-Forbidden Evidence Hardening v1

Status, 2026-06-30: completed as a shadow/review evidence layer. Not promoted.

## Result

The v1 hardening layer turned the first ASR-positive Echo Guard vNext result into normal pipeline
evidence:

- `remote_forbidden_evidence.jsonl` stores persistent rows with timestamps, remote/mic tokens,
  speaker state, transcript links, confidence and decision reason;
- `remote_forbidden_summary.json` stores per-session leakage before/after, local recall, guarded
  seconds, review-burden seconds and gate state;
- `remote_forbidden_review.md` gives a readable review artifact;
- `murmurmark audit remote-forbidden` materializes the layer from the CLI;
- `murmurmark status/report` exposes the remaining risk and links the review artifact;
- `report-remote-forbidden-corpus.py` writes the corpus summary and explicitly explains why fewer
  than two sessions are safely improved.

Six-session corpus:

- `reports_found = 6/6`;
- `safe_improved_sessions = 1/6`;
- `local_recall_regressions = 0/6`;
- `guarded_seconds = 28.0`;
- `review_burden_seconds = 18.0`;
- `promotion_decision = shadow_review_only_do_not_promote`.

Interpretation: the evidence contract is now real. The next weakness is coverage, not persistence.

---

# Previous Completed Goal: Echo Guard Complete Removal vNext

Status, 2026-06-30: completed as a shadow research spike. Not promoted.

## Result

The vNext spike added:

- `segment_switch_remote_floor_local_fir`: a shadow audio candidate that uses `remote_floor` only in
  `remote_only` windows and keeps `local_fir` elsewhere;
- `remote_forbidden_token_guard`: a virtual ASR safety candidate for audit windows that removes
  forbidden remote-reference tokens only in `remote_only` regions;
- per-session and corpus reports comparing the result with `local_fir` and v0 candidates.

Smoke corpus:

- `sessions/2026-06-23_14-04-37`;
- `sessions/2026-06-26_15-32-02`;
- `sessions/2026-06-29_15-46-17`;
- `sessions/2026-06-29_16-31-02`;
- `sessions/2026-06-30_11-15-56`;
- `sessions/2026-06-30_17-17-20`.

Outcome:

- `remote_forbidden_token_guard` passed ASR-token gates on one difficult 1x1 session:
  `remote_token_leak_delta = -0.5`, `local_only_word_recall_delta = 0.0`;
- hardened corpus summary: `safe_improved_sessions = 1/6`,
  `remote_token_leak_improved_sessions = 1/6`, `local_recall_regressions = 0/6`;
- the other sampled sessions currently explain as `no_baseline_asr_visible_leak`: the selected
  ASR-positive windows do not contain measurable remote-token leakage in the `local_fir` baseline, so
  they cannot safely count as fixed cases yet;
- no audio candidate beat `local_fir` well enough for promotion.

Interpretation: vNext proved the right measurement direction. It did not solve complete echo
removal. The next step is broader window selection and stronger evidence, not default replacement.

---

# Previous Completed Goal: Export Bundle Quality v1

Status, 2026-06-30: completed.

MurmurMark now ends a successful meeting pipeline with a local handoff bundle that can be read as
the user-facing result, not as a directory of internal derived artifacts.

## Goal

After `record`, `process` and the minimal required review, the user should be able to run:

```bash
murmurmark finish SESSION
```

The resulting Markdown or Obsidian bundle should answer:

- can this result be used;
- which transcript profile was selected;
- what the verdict and review burden are;
- which notes, decisions, actions, risks and open questions were extracted;
- which utterance IDs support the notes;
- which regions still need review;
- where the full transcript is;
- what the retention/privacy next step is.

## Completed Scope

- `index.md` is now the start page with "Can I use this?", selected profile, verdict, review burden,
  review-needed items, retention/privacy summary and the next command.
- `quality_verdict.md` is rendered as a human trust report rather than a raw metrics dump.
- `notes.md` is rendered as a readable extractive working summary with evidence IDs and review
  markers.
- `transcript.md` is rendered from `clean_dialogue*.json` when available and keeps roles, timestamps,
  utterance IDs, source track and review flags.
- Obsidian export writes one self-contained frontmatter note with verdict, notes, review items and
  transcript content.
- `export_manifest.json` records `bundle_quality: "v1"`.
- Export still blocks honestly when readiness has blockers. Forced exports remain debug artifacts and
  say "Do not use yet".
- Raw audio is not copied into export bundles.

## Commit

`6362d12 feat: improve export bundle handoff`
