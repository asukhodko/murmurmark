# Reliable Transcription Route

Status: active product route; durable capture, one-command processing, evidence handoff and guarded
export are complete. Speaker-Preserving Neural Echo v2 is the production pre-ASR plateau.
Alignment/Echo-Path v3 is complete with `READY_FOR_MULTI_COMPONENT_SEPARATOR`; the current bounded
audio step is Multi-Component Residual Separator Qualification v1.
Date: 2026-08-06

Consultation synthesis: Gemini, GPT-Pro and Fable converged on deterministic outcomes,
corpus-calibrated gates and explicit review burden before broader repair. Outcome Contract v1,
Reliable Processing UX v1, resumable ASR and durable committed-PCM Live Evidence are implemented.
The stable production path is intentionally boring: `murmurmark meeting` produces a usable
two-track session and final result, or fails explicitly before publication. Order/role
reconciliation classified all `23` auditable
rows and reduced the `15` previous effective blockers to `0` without changing turns. Local-recall
hardening then classified all `118` bounded rows and materialized the causal remote-energy shadow
profile. It reduces aggregate missing `Me` from `2844.88s` to `2166.56s` without increasing
remote-like `Me`, effective order blockers, review burden or per-session token-F1 regressions. The
v2 local-island pass then classified all `40` unresolved rows and reduced missing `Me` to
`1910.79s`, while remote-like `Me` stayed at `108.42s` and effective order blockers stayed at `0`.
Remote-active separation then classified all `19` primary and `16` mixed/double-talk cross-check
rows, accepted `9` primary rows and reduced missing `Me` to `1657.89s` with the same remote, order,
token-F1 and review-burden gates. Their recording-time integration now runs as a bounded latest-only
child after the base live chunk is durable. Paced replay reproduces candidate sets and profile
metrics across all seven sessions; failures and lag affect only the explicit diagnostic shadow. The
runtime now reuses a content-addressed stable prefix and processes only new/invalidated suffix
evidence. Warm equivalence passes `7/7`, and a stride-1 source-time run gives `p95=13.61s` and
maximum `19.16s`. Three fresh real sessions subsequently proved pre-stop execution and zero final
lag. Causal Double-Talk Me Recovery v1 now gives all `16` fixed overlap rows stable outcomes and
safely recovers `4` rows / `11.56s`; aggregate missing `Me` falls to `1639.73s`, remote-like `Me`
stays `108.42s`, order blockers stay `0`, review burden falls to `478.82s`, and runtime p95 is
`23.473s` with final lag `0`. Generalization then produced `963` stable outcomes, preserved all
`832` frozen input hashes and accepted zero of `65` adversarial negative controls. It still returned
`DO_NOT_PROMOTE`: only `268/783` eligible rows reached the expensive candidate stage, all three
holdout runtime replays timed out fail-open, and one holdout gained an effective order blocker. The
follow-up prefilter classified all `783` rows and removed that order regression. All `65` frozen
negative controls remain rejected, but one new candidate is post-hoc probable ASR noise; none of the
three holdouts passed runtime gates, with `20` fail-open timeouts and p95 up to `42.634s`.
Near-realtime output therefore remains shadow-only. The bounded product
question is now how much authoritative batch order/boundary review can be closed safely from existing
source segments and audio evidence.

## Why This Exists

MurmurMark is already useful, but it still asks too much from the operator. A user can record a real
meeting and receive notes, yet the pipeline may take a long time, require review, block export, or
leave the user unsure whether the transcript can be trusted.

The next product target is not "one more clever cleanup heuristic". The target is a dependable route:

```text
record meeting -> process unattended -> get a transcript, notes, verdict and exact next action
```

If the result is good, MurmurMark should say so. If the result is risky, it should say exactly why,
how much review remains, and whether the recording is still useful. It must not silently turn a bad
transcript into a confident artifact.

## Reliability Promise

For a supported macOS setup and a complete two-track recording, MurmurMark should produce one of
three outcomes without the user supervising internal stages:

1. **Ready for notes.** Notes and selected evidence are safe enough for ordinary internal follow-up.
2. **Review first.** The transcript is useful, but a short explicit review queue blocks medium-risk
   use or full export.
3. **Do not use without manual review.** The recording or transcript has a clear blocker.

Every outcome must include:

- selected transcript profile;
- quality verdict;
- review burden in seconds and rows;
- exact files to open;
- exact next command;
- reason for export blocking, if any;
- retention state.

## What "Simply Works" Means

The user should not have to watch ASR progress or understand which repair profile won. They should be
able to run:

```bash
murmurmark meeting --target-bundle system
```

The pipeline can still take time. The reliability requirement is that it is resumable, observable and
honest:

- long-running stages show progress and can be resumed from verified ASR chunks;
- already completed stages are reused unless `--force-*` is explicit;
- missing optional models degrade gracefully;
- partial recordings are marked partial and blocked by default;
- silent captures are blocked before ASR and never become empty successful transcripts;
- no derived profile is promoted without corpus gates;
- raw CAF tracks are never modified by processing.

## Current Weak Points

The main remaining blockers are consistent across recent sessions:

- remote speech still leaks into `mic` and creates false `Me` fragments;
- long `Me` turns can cross remote turns and create chronology risk;
- opening and boundary repair does not cover every short greeting/check phrase;
- suggested review can reduce the queue, but not all rows are closable by current local evidence;
- `process` is batch-first and expensive for long meetings;
- several diagnostics exist, but their implications are still scattered across reports.

The completed live-recovery experiment is narrower and measurable:

- all `783` eligible rows now have causal routes, and the earlier order regression is removed;
- all `65` frozen negative controls remain rejected, but one new accepted candidate fails post-hoc
  evaluation as probable ASR noise;
- none of the three holdouts passes every runtime gate; p95 reaches `42.634s` and `20` expensive
  attempts time out fail-open;
- final lag remains zero and normal preview remains disconnected.

## Current Bounded Step

Authoritative Transcript Boundary and Review Closure v1 is complete. Its frozen operational queue
contained `337` rows / `1731.892s`; source ASR segments, speaker state, review audio and prior
decisions safely closed `213` rows / `1253.620s`. The promoted `authoritative_boundary_v1` profile
keeps the remaining `124` rows / `478.272s` explicit and passes every per-session remote-like `Me`,
order, local-recall, local-content, notes and export gate.

Residual Me Evidence Closure v1 is complete with `PROMOTE_RESIDUAL_ME_EVIDENCE_V1`. It gives every
residual row exact local evidence and safely closes another `31` rows / `170.589s`: `21`
local-recall, `9` order and `1` audio-review row. The selected residual profile leaves `93` rows /
`307.683s` explicit and passes every frozen-input, local-content, order, notes and export gate.

Residual Audio Evidence Arbitration v1 is complete with reproducible `DO_NOT_PROMOTE`. It classified
all `66` audio-review rows / `196.920s`, but independent Target-Me, bounded word-timestamp ASR and
remote-forbidden evidence safely closed only `1` row / `0.640s`. The input
`residual_me_evidence_v1` profile therefore remained the safe input to Residual Local Recall
Closure v1. That pass completed with `PROMOTE_RESIDUAL_LOCAL_RECALL_V1`: all `13` rows / `48.073s`
received stable outcomes, and `9` rows / `26.953s` closed as already covered, paraphrased or
remote-supported. It inserted no speech, preserved all verdicts and note evidence, and left four
ambiguous rows explicit.

Residual Chronology Closure and Speaker-Mode Hardening have since established the remaining
evidence ceiling. Evidence-Backed Me Completion v2 is promoted for its frozen two-session scope:
it safely closes `3/6` local-recall rows / `22.4/35.85s`, repairs one duplicate text tail and turns
all unresolved `Me` text into concrete review lanes. `residual_local_recall_v1` remains fallback
outside that scope. Mixed-Utterance Remote Span Separation v1 then froze `12` mixed `Me` rows /
`54.940s` and classified all of them, but completed with `DO_NOT_PROMOTE`: remote spans were often
visible while the identity of every retained local prefix/tail could not be proven independently.
No transcript edit was applied.

Echo Suppression Promotion v1 moved the intervention before ASR and completed with reproducible
`DO_NOT_PROMOTE`. Its best candidate reduced bounded ASR-visible remote-risk by `68.2845%`, but
passed only `3/5` speaker sessions: protected-local retention fell to `45.45%` and chronology recall
to `0%` on the counterexamples.

Neural Residual Echo Suppression v1 reused the signed timeline, exact `local_fir` baseline and
frozen failures with a pinned Microsoft DEC model. It removed all bounded remote-risk in the two
hard sessions, but protected-local recall fell to `45.45%`, chronology and double-talk recall to
`0%`, and incremental runtime reached `52.85%`. The reproducible `DO_NOT_PROMOTE` rules out a
simple pretrained-engine swap.

Speaker-Preserving Echo Adaptation Corpus v1 completed with reproducible `DO_NOT_TRAIN`.
Session-disjoint splits, privacy checks and local-only target coverage passed, but no remote-only
interval passed the frozen confidence gate. Synthetic pairing therefore remained forbidden;
hard-test coverage also stopped at `6s` double-talk and no independently confirmed opening
acknowledgement. Replay matched `414/414` files, no training ran and production stayed on
`local_fir`.

Controlled Echo Supervision Lab v1 supplied the missing evidence and completed with
`READY_FOR_ADAPTATION`. Speaker-Preserving Neural Echo v2 then completed with guarded
`PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2`. Its personalized hybrid selected candidate audio in
`5/12` sealed corpus sessions, removed `41.940s` and `90` remote-supported tokens, and retained all
candidate local tokens. The other `7/12` sessions used exact fallback. The candidate clean mic is
transcribed directly and downstream deletion receives zero suppression credit. Missing enrollment,
headphones, stale fingerprints or any preservation regression keep `local_fir_role_masked`.

Reference-Conditioned Target-Me Separation v1 completed with `DO_NOT_PROMOTE`. Oracle and overfit
passed, but two train/dev candidates missed locked source gates. More importantly, one fixed
enrollment and zero independently labelled non-target local-speech rows could not prove semantic
speaker attribution. Hard-test and the sealed corpus remained unopened; Speaker-Preserving Neural
Echo v2 stayed byte-exact.

Target-Me Identifiability Corpus v1 closed that prerequisite with
`READY_FOR_TARGET_CONDITIONED_TRAINING`: `4/2/2` split-disjoint non-target speakers,
`1200/300/300s` full mixtures and `980` paired correct/wrong enrollment queries passed exact replay
and contamination gates. It did not alter `mic_for_asr.wav` or production.

Reference-Conditioned Target-Me Separation v2 has now completed with reproducible
`DO_NOT_PROMOTE`. The paired corpus proved enrollment adherence (`4.991 dB` median query margin,
zero collapse), but the bounded spectral candidate reached only `4.852 dB` Target-Me and
`4.107 dB` non-target SNR. It failed dev before hard access, so the sealed meetings and production
audio remained untouched.

Evidence Notes And Export v2 now binds the selected transcript, verdict, review burden,
evidence-backed notes and export readiness in one fingerprinted product boundary. Its 110-session
corpus has zero referential-integrity, stale-manifest and deterministic-replay failures.
Release-quality CLI then made this boundary installable, upgrade-safe and verifiable from a
packaged release. Reliable Final Handoff v1 separated optional work, added bounded resume and made
terminal review actionable. Product handoff is therefore stable enough to return the critical path
to the earlier audio boundary. Pre-ASR Target-Me Isolation Limit v1 now treats Speaker-Preserving
Neural Echo v2 as the exact production plateau. Its completed residual map selected
`READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3`: alignment and echo-path capability accounts for
`2443.222s` (`35.567%`) of actionable evidence, ahead of multi-component separation and Target-Me
model work. Alignment/Echo-Path v3 then reached `READY_FOR_MULTI_COMPONENT_SEPARATOR`: it safely
changed 11/32 controlled remote items instead of the required 12 and retained 156/156 protected
items exactly; the required low-leak control changed instead of exact fallback. Hard/sealed data
stayed closed. Cold first-pass ASR latency and failed live-recovery
profiles remain secondary constraints, not reasons to accept remote leakage or lose local speech.

## Route To Reliability

### 1. Make The Outcome Contract First-Class

Current v1 creates one stable "transcription outcome" contract for every processed session:

```text
ready_for_notes | review_first | blocked | partial
```

It is derived from existing readiness, quality verdict, review progress, export blockers and the
latest pipeline report. `status`, `next`, `report`, `finish`, `outcome` and `report corpus --refresh`
now expose or refresh the same next action. Guarded export still uses readiness/export blockers as
the hard safety gate and should continue converging on the same contract.

Acceptance:

- no conflicting recommendation between `status`, `next`, `finish` and corpus report;
- every blocker has a remediation command or an explicit "manual review required";
- no command suggests export when export blockers remain.

Concrete v1 artifacts:

```text
derived/outcome/
  outcome.json
  outcome.md
  review_plan.json
  next_command.txt
```

`outcome.json` separates:

- transcript readiness;
- notes readiness;
- export readiness;
- retention status, currently conservative until retention planning runs;
- selected transcript/audio/notes profiles;
- gate reasons;
- review lanes and estimated minutes;
- pipeline/resume state.

If required inputs are missing or a stage failed, the route still writes an outcome with
`pipeline_failed`/`blocked` and a resume or inspection command. The user should never be left reading
raw logs to know what happened.

CLI entry points:

```bash
murmurmark outcome SESSION|latest
murmurmark outcome SESSION|latest --refresh
```

`murmurmark process` writes the artifacts automatically at the end of a run. `murmurmark report` and
`murmurmark next --refresh` refresh them together with `session_readiness`.

`murmurmark export` and `murmurmark finish` must obey the same contract. A normal export is allowed
only when `outcome.json` says `ready_for_notes` and `export_status: allowed`; otherwise the export
writes a blocked report with the exact next command from the outcome contract. `--force` remains a
debugging escape hatch, not the normal product path.

### 2. Reduce Mandatory Review At The Root

Downstream cleanup helps, but it is expensive. The strongest path is still to reduce remote-derived
words before they become `Me` text.

Near-term work:

- keep `local_fir` as default;
- keep `coverage_v2_remote_gate_local_fir` shadow-only;
- widen ASR-positive echo-candidate validation over more real sessions;
- compare token leakage, local word recall, order risk and review burden;
- define promotion, rollback and inspection rules before any default change.

Acceptance:

- corpus report explains every improved, blocked and not-applicable session;
- local recall never regresses silently;
- candidate cannot become default while any gate is unknown.

Important guardrail from the consultation: do not promote because audio metrics look cleaner. Promote
only if ASR-visible remote leakage decreases, local speech recall does not regress, order risk does
not grow and the downstream outcome improves.

### 3. Close More Review Rows Automatically, But Only With Evidence

The local stronger audio judge and Target-Me evidence are useful because they can protect real `Me`
speech and identify obvious remote duplicates.

Near-term work:

- route all review rows through the same evidence matcher;
- make `review suggested apply` cumulative and safe by default;
- add targeted local judges only where they can reduce the queue;
- keep uncertain rows explicit.

Acceptance:

- suggested closure never rewrites rows that are already reviewed;
- generated decisions carry source audit IDs;
- auto-closed rows lower review burden without increasing lost-Me or order risk.

Review lanes should be outcome-facing, not just diagnostic. The first useful lane set is:

```text
critical
order
me_role_risk
notes_impacting
overlap_review
local_recall
low_priority
```

The report should show review burden by lane, hide low-priority noise by default, and keep exact
commands for the first lane that blocks the chosen outcome.

### 4. Treat Transcript Order As A Product Blocker

Many remaining risks are not "bad ASR" but "wrong sequence". The product must not pretend a reply is
chronologically safe when a long `Me` segment crosses a remote reply.

Near-term work:

- strengthen order repair for source-backed splits;
- make uncertain order rows the first review lane;
- add corpus regression cases for known long-Me-crosses-remote patterns;
- keep "needs review" when split evidence is insufficient.

Acceptance:

- `transcript_order_risk` is either repaired, reviewed or remains an explicit blocker;
- no long cross-role overlap can silently pass as clean transcript.

### 5. Make Long Processing Less Fragile

Batch processing remains authoritative, but the user should not feel that a long ASR stage is a
black box.

Near-term work:

- keep ASR chunk cache and rebuild checks as hard gates;
- expand chunk-cache coverage over the real corpus;
- keep `--live-pipeline` quarantined until the async bounded segment queue proves capture-safety;
- do not collect real live-pipeline meetings while live capture-safety and parity evidence is missing;
- after the proof, compare live draft to batch output through corpus gates.

Acceptance:

- interrupted processing can be resumed with one command and reused chunks are visible in reports;
- `process` explains whether it is recomputing or reusing cache;
- live mode never weakens batch readiness gates.

Minimum run-state contract:

```text
derived/run/pipeline_run.json
```

Current v1 contains step ids, status, timestamps, durations, outcome, next command, session-level
resume command, expected output checkpoints, missing output count and a basic stuck-state summary.
Chunked/Resumable Processing v1 adds stable ASR cache metadata, verified chunk rebuilds and
process-level failure/`Ctrl-C` resume. The remaining hardening work is broader corpus coverage and,
later, a capture-safe near-realtime redesign before live chunks can again be studied as a batch-grade
cache source.

## Gate Model v1

The gate evaluator should be a deterministic function:

```text
metrics + artifacts + policy -> outcome
```

Use monotonic gates first. Avoid learned blended scores until there is enough labeled data.

Gate layers:

```text
hard gates
risk gates
review burden gates
notes gates
export/retention gates
```

Recommended hard gates:

- capture artifacts exist and raw CAF is readable;
- selected `clean_dialogue` exists and has a compatible schema;
- notes evidence IDs all exist;
- selected profile is internally consistent;
- `unrepaired_long_mic_crossings_count == 0`;
- `golden_phrase_fail_count == 0`;
- critical transcript-order failures are zero.

If a required metric cannot be computed, the outcome should degrade to `review_first` or `blocked`,
never improve.

The five primary readiness metrics should be:

1. `harmful_remote_in_me_sec`: remaining probable duplicate, high-confidence remote leak and ASR
   noise inside `Me`;
2. `order_risk`: unrepaired long crossings, critical order count and conflict seconds;
3. `local_recall`: local-only island recall, short local island recall and possible lost local speech;
4. `review_burden`: estimated review seconds/minutes by lane;
5. `notes_evidence_integrity`: selected note evidence ids still point to existing utterances.

Other metrics can stay diagnostic.

## Corpus v0 And Labels

Reliable gates need a small labeled operating corpus. Start with 12-20 real sessions, not synthetic
fixtures only:

- 1x1 meetings;
- group meetings;
- noisy/open-space meetings;
- sessions with heavy short acknowledgements;
- sessions with known remote leak/order risks.

Each review decision should be preserved as labels, not thrown away after one session. The labels
store should capture:

```text
attribution_correct
text_usable
order_correct
local_speech_deleted_or_missing
remote_duplicate_or_leak
review_decision
source_audit_ids
```

This is the flywheel: review burden produces the data needed to reduce future review burden.

## Current Executable Goal

```text
Pre-ASR Target-Me Isolation Limit v1: сохранить production v2, residual map и завершённый
Alignment/Echo-Path v3 неизменными; провести Multi-Component Residual Separator Qualification v1 с
явными Target-Me, remote-echo, other-local и residual stems, correct/wrong query controls, mixture
consistency и exact fallback. Завершить PROMOTE_MULTI_COMPONENT_RESIDUAL_SEPARATOR,
READY_FOR_STRONGER_LOCAL_SEPARATOR либо CURRENT_RESOURCE_LIMIT_REACHED без потери protected Me,
nearby speech, chronology, openings, double-talk и без post-ASR cleanup credit.
```

## Consultation Prompt

Use this prompt if external consultation is needed:

```text
Мы строим MurmurMark: локальный macOS CLI-пайплайн для рабочих созвонов. Сейчас целиком
фокусируемся на пред-ASR отделении целевого пользователя Me от акустического remote leakage.

Имеется:
- неизменяемые raw mic/remote CAF и authoritative digital remote;
- local_fir, delay trajectory, speaker state и exact fallback;
- private Target-Me enrollment, WavLM/Resemblyzer и controlled echo supervision;
- promoted Speaker-Preserving Neural Echo v2: 5/12 candidate, 7/12 fallback, local retention 1.0;
- frozen Residual Echo Ceiling Map: 14 real sessions, 2068 material events, 6869.306s actionable;
- capability ordering: alignment/echo path 35.567%, multi-component 30.923%, Target-Me 18.324%;
- Alignment/Echo-Path v3 завершён READY_FOR_MULTI_COMPONENT_SEPARATOR: 11/32 controlled remote
  items вместо gate 12, median reduction 2.552124 dB, protected exact 156/156, required low-leak
  control failed exact fallback, hard/sealed закрыты;
- Target-Me Identifiability Corpus даёт split-disjoint correct/wrong-query и non-target controls;
- Reference-Conditioned v2 доказал query adherence, но scratch FiLM+GRU не прошёл quality dev;
- direct local whisper.cpp large-v3 q5_0 и frozen chronology/double-talk/opening/no-speech gates.

North Star: canonical mic для ASR сохраняет каждое подтверждённое слово Me, не содержит
распознаваемого authoritative remote и не относит nearby other-local speech к Me. Неизвестное
остаётся explicit residual; недостаток доказательств выбирает exact production fallback.

Ограничения:
- raw CAF не менять;
- всё локально/offline на Apple Silicon, cloud audio запрещён;
- post-ASR cleanup не получает promotion credit;
- потеря protected Me, chronology, opening или double-talk запрещает promotion;
- hard/sealed data нельзя открывать до locked dev pass;
- residual-map thresholds и capability ordering нельзя настраивать по результатам candidate;
- ещё один scalar mask, larger FIR bank или post-ASR cleanup не рассматриваются.

Вопросы:
1. Какой минимальный four-stem contract лучше задаёт Target-Me, remote echo, nearby other-local и
   unexplained residual при одном mic и exact digital remote?
2. Как объединить v3 echo estimate, complex mixture, remote reference и Target-Me query без
   identity collapse и без принудительного возврата remote через mixture consistency?
3. Какой bounded candidate ladder реалистичен локально на Apple Silicon: constrained baseline,
   reference-conditioned separator и проверенная pretrained initialization?
4. Как построить split-disjoint supervision из имеющихся measured echo, clean Target-Me,
   non-target speakers и wrong-query controls без synthetic-to-real leakage?
5. Какие audio, direct-ASR, speaker-attribution и runtime gates должны пройти dev до hard/sealed?
6. Какой terminal negative result точно отделит нехватку архитектуры от нехватки локальных
   вычислительных ресурсов или supervision?
```
