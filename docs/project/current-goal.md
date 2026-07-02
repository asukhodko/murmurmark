# Current Goal: Reliable Transcription Route v1

Status, 2026-07-02: completed as v1. Outcome Contract v1, Gate Evaluator, Review Plan,
Next Command, session-level Run Manifest and outcome-aware guarded export are implemented.
The first follow-up, Review Burden Reduction v1, is also complete: the operational corpus now has a
short explicit manual tail and no pending safe suggestions.

Goal:

```text
Reliable Transcription Route v1: превратить текущий набор record/process/audit/review/export
слоёв в один надёжный unattended маршрут от полной записи до честного результата. Реализовать
Outcome Contract v1, Gate Evaluator, Review Plan, Next Command и Resumable Run Manifest. Не менять
raw CAF, default local_fir и основной whisper.cpp ASR без отдельных corpus gates.
```

Plainly: the user should not have to supervise MurmurMark while it turns a recording into a
transcript. A complete recording should lead to a clear result: usable notes, explicit short review,
or an honest blocker. Long ASR should not look like a hang, export should not be offered when review
is still required, and every remaining risk should point to an exact next command.

Why this is the right next goal:

- the CLI product is already real enough for pilot use, but not yet boring enough for daily trust;
- recent sessions show the same pattern: capture and processing complete, but transcript order,
  remote leak and review burden still decide whether the result can be used unattended;
- adding more downstream cleanup alone will not converge unless the outcome contract and corpus gates
  make "good enough" explicit;
- ASR-positive Echo candidates, Target-Me evidence and stronger audio judge are now useful inputs to
  a reliability route, not separate ends in themselves.

Definition of done:

- every processed session writes `derived/outcome/outcome.json`, `outcome.md`,
  `review_plan.json` and `next_command.txt`;
- `murmurmark status`, `next`, `outcome`, `finish`, `report session` and `report corpus --refresh`
  expose or refresh the same outcome next action;
- interrupted processing can be resumed without guessing which stage finished - implemented at
  session-step/output-checkpoint level; ASR chunk/window resume remains future hardening;
- corpus reports separate `ready_for_notes`, `review_first`, `blocked`, partial and diagnostic
  sessions without hidden exceptions;
- exported bundles are blocked unless the chosen outcome allows export;
- `local_fir` remains the default Echo Guard path.

Follow-up hardening:

- calibrate the first gates on corpus v0 labels;
- add ASR chunk/window progress and resume checkpoints;
- widen ASR-positive Echo candidate validation, still without default promotion;
- reduce mandatory review burden further at the audio source instead of adding more downstream
  cleanup.

Primary design document:

- [Reliable Transcription Route](reliable-transcription-route.md)

Consultation prompt:

- [Reliable Transcription Route / Consultation Prompt](reliable-transcription-route.md#consultation-prompt)

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
