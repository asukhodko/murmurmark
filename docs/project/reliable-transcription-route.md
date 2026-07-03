# Reliable Transcription Route

Status: active product route; Outcome/Processing UX v1 and Chunked/Resumable Processing v1 complete,
live parity coverage next
Date: 2026-07-03

Consultation synthesis: Gemini, GPT-Pro and Fable agreed on the same practical direction. Do not add
another broad repair layer first. Turn the existing audits into one deterministic outcome contract,
then use corpus-calibrated gates and review-burden telemetry to decide whether a transcript is ready,
needs review or is blocked. Outcome Contract v1 and Reliable Processing UX v1 are now implemented.
The next concrete reliability gap is live parity coverage: near-realtime chunks already exist as a
shadow path, but they need real-session comparisons before they can be trusted as a batch-grade
cache source.

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
murmurmark record --target-bundle system
murmurmark process latest
murmurmark status latest
murmurmark finish latest
```

The pipeline can still take time. The reliability requirement is that it is resumable, observable and
honest:

- long-running stages show progress and can be resumed from verified ASR chunks;
- already completed stages are reused unless `--force-*` is explicit;
- missing optional models degrade gracefully;
- partial recordings are marked partial and blocked by default;
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
- collect real live-pipeline sessions with `live_batch_comparison.json`;
- keep live pipeline as shadow acceleration, not as the source of truth;
- compare live draft to batch output through corpus gates.

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
process-level failure/`Ctrl-C` resume. The remaining hardening work is broader corpus coverage and
real live-cache parity gates before near-realtime chunks can be trusted as a batch-grade cache
source.

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

## Suggested Next Goal

```text
Reliable Transcription Route v1: превратить текущий набор record/process/audit/review/export
слоёв в один надёжный unattended маршрут от полной записи до честного результата. Реализовать
Outcome Contract v1, Gate Evaluator, Review Plan, Next Command и Resumable Run Manifest; откалибровать
первые gates на corpus v0 и закрывать автоматически только доказанные review rows. Не менять raw CAF,
default local_fir и основной whisper.cpp ASR без отдельных corpus gates.
```

## Consultation Prompt

Use this prompt if external consultation is needed:

```text
Мы строим MurmurMark: локальный macOS CLI-пайплайн для превращения рабочих созвонов в
транскрибацию, заметки и evidence-backed артефакты без облачного рекордера.

Текущая архитектура:
- запись двух дорожек: mic и remote в raw CAF;
- raw CAF неизменяемы;
- Echo Guard local_fir создаёт mic_for_asr;
- основной ASR: локальный whisper.cpp large-v3 q5_0, русский язык;
- remote считается authoritative для Colleagues, mic — candidate source для Me;
- есть timeline repair, start-of-call repair, audit cleanup profiles, group overlap audit,
  local recall audit, transcript order audit, audio-review pack, optional faster-whisper
  stronger audio judge, Target-Me evidence, extractive notes, quality verdict, review lanes,
  guarded export и retention plan;
- pipeline уже работает на реальных встречах, но не всегда unattended: иногда остаются
  order risks, remote leak в mic, короткий review burden, заблокированный export.

Новая продуктовая цель:
сделать не “ещё одну эвристику”, а надёжный маршрут:
record meeting -> process unattended -> получить честный outcome:
ready_for_notes / review_first / do_not_use_without_manual_review,
с точным next command, review burden и export/retention status.

Ограничения:
- raw CAF не менять;
- cloud ASR/LLM не использовать по умолчанию;
- default local_fir и основной whisper.cpp ASR не менять без corpus gates;
- лучше честный review_first, чем уверенный неправильный transcript;
- UI не приоритет, CLI-first.

Вопросы:
1. Как бы вы спроектировали outcome contract и gates, чтобы пользователь не контролировал
   пайплайн вручную, но система не скрывала риск?
2. Какие 3-5 метрик лучше всего предсказывают, что transcript уже достаточно надёжен для
   заметок и для полного export?
3. Как лучше уменьшать mandatory review burden: через echo suppression, order repair, local
   speaker evidence, stronger ASR judge, forced alignment или иной слой?
4. Какой safe promotion path нужен для shadow audio candidate, который снижает ASR-visible remote
   leakage, но может не помогать на части сессий?
5. Как организовать resumable/progress-aware batch pipeline, чтобы long ASR не выглядел как
   зависание?
6. Какие ошибки в такой архитектуре чаще всего будут создавать ложное чувство готовности?
7. Какой минимальный следующий milestone вы бы выбрали, чтобы приблизиться к “просто работает”
   без большой смены архитектуры?
```
