# Simple whisper.cpp Transcription Runbook

Use this path for local post-recording transcription, cleanup, quality verdicts and extractive notes.

The goal is a useful local transcript and an evidence-backed notes package, not final diarization or
polished generative notes.

## Preconditions

Install `whisper.cpp`:

```bash
brew install whisper-cpp
```

Download at least one multilingual GGML model:

```text
~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin
```

Use the same `large-v3-q5_0` model for normal runs and debugging. This keeps model choice out of
the list of possible causes when comparing results.

## Recommended Flow

Start from a completed session and run the current full post-recording pipeline:

```bash
SESSION=./sessions/<session>
MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"

.venv/bin/python scripts/run-session-pipeline.py "$SESSION" \
  --model "$MODEL" \
  --language ru

jq '{status, outputs}' "$SESSION/derived/pipeline-run/pipeline_run_report.json"
less "$SESSION/derived/readiness/session_readiness.md"
```

The runner calls Echo Guard, export/transcription, shadow timeline repair, local-recall audit,
group-overlap audit, audio-review audit, `audit_cleanup_v1..v4`, extractive synthesis and per-session readiness. Use
`--force-asr` when you need to regenerate Whisper output, and `--reuse-asr-cache` when you only want
to rebuild repair, cleanup, synthesis and reports from cached ASR JSON.

Read `derived/readiness/session_readiness.md` before using a meeting result. It contains:

- `ready_for_notes`: the notes can be used with normal caution;
- `review_first`: check the listed audio-review regions before using the result for medium-risk work;
- `do_not_use_without_manual_review`: the transcript is too risky for unattended use;
- `pipeline_incomplete`: rerun the full pipeline before judging the session.

## Low-Level Transcription

Use the lower-level commands below when debugging a specific layer. If the call was recorded through
speakers, run Echo Guard first:

```bash
SESSION=./sessions/<session>

.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
```

Run simple transcription:

```bash
MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru
```

The script does three things:

1. Runs `murmurmark export-audio`.
2. Runs `whisper-cli` for `derived/asr/mic.wav` and `derived/asr/remote.wav`.
3. Builds raw segments, candidate utterances, role decisions, clean dialogue, and Markdown.

By default the bridge runs `whisper-cli` with `--max-context 0`, short ASR windows, and a
small overlap between windows. This avoids feeding a whole meeting into Whisper as one long file,
which has produced worse timestamps, missing short replies, and context drift in real MurmurMark
sessions.

Default ASR settings:

```text
--asr-mode windowed
--asr-window-sec 60
--asr-overlap-sec 5
--mic-audio-prep speech
--remote-audio-prep loudnorm
```

`remote` is normalized only for ASR input. Raw capture and `derived/asr/remote.wav` are not changed.
`mic` uses only speech-band filtering by default: high-pass, low-pass, and a safety limiter. Full-file
mic loudnorm is intentionally not enabled because it can amplify residual remote bleed in the cleaned
mic track.

For backend/platform calls, pass the compact domain prompt file:

```bash
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"
```

The prompt is derived from the local domain pack and terms observed in current MurmurMark sessions:
Kubernetes, staging, throttling/logging vocabulary, internal names, people names, and recurring
work/health terms. It is intentionally short because `whisper.cpp` uses it as ASR context, not as a
post-processing dictionary.

To reproduce the old whole-track mode:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --asr-mode whole \
  --remote-audio-prep none
```

Raw Whisper results are cached in `raw/mic.json` and `raw/remote.json`. The cache is reused only
when the model, language, prompt, duration limit, `--max-context`, ASR window settings, and audio
preparation settings match the current run.
Use `--force` to regenerate raw Whisper output anyway. Changing `--prompt-file` also changes the
raw cache metadata, so the next non-skipped run will regenerate raw ASR output.

To test the next repair logic without changing the main transcript, run the shadow profile after the
normal transcript exists:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --skip-export \
  --skip-transcribe \
  --repair-profile shadow_v2
```

`current` remains the default profile. The shadow profile writes separate `*.shadow_v2.*` artifacts
and `repair_comparison.json`; `transcript.md` remains the baseline. The core raw/candidate/role
JSON files are also written with the same suffix so the shadow result is auditable without mixing it
with the baseline artifacts.

Build the local extractive synthesis package directly from the best safe transcript profile:

```bash
scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile auto

jq '{verdict, selected_transcript_profile, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

## Local Recall Audit

Timeline repair can report low `local_only_island_recall` when a long `Me` candidate is split around
remote speech and some short local-only islands are not recovered as separate `Me` utterances. Run
the local recall audit to separate likely harmless short/weak islands from possible lost local
speech:

```bash
.venv/bin/python scripts/audit-local-recall.py "$SESSION" --profile shadow_v2

jq '.summary' "$SESSION/derived/audit/local-recall/local_recall_audit.json"
less "$SESSION/derived/audit/local-recall/local_recall_review.md"
```

The audit reads only timeline-repair examples and Echo Guard `speaker_state.jsonl`. It writes under
`derived/audit/local-recall/`, does not edit transcripts, and is used by
`report-session-quality.py` to decide whether low local recall should block `ready_for_notes`.

## Group Overlap Audit

For group calls, `remote_duplicate_in_me_seconds` can overstate the real damage. Some overlap is
normal double-talk, and some is only timestamp boundary overlap between adjacent turns. Run the
group audit after transcription and synthesis when you need to separate harmful duplicates from
expected group-call overlap:

```bash
.venv/bin/python scripts/audit-group-overlaps.py "$SESSION" \
  --profile shadow_v2 \
  --min-overlap-sec 0.5 \
  --review-threshold-sec 2.0 \
  --write-clips \
  --max-clips 80

jq '{classified, harmful, benign_or_expected, review, recommended_verdict_adjustment}' \
  "$SESSION/derived/audit/group-overlaps/group_overlap_summary.json"

less "$SESSION/derived/audit/group-overlaps/group_overlap_review.md"
```

The audit reads transcript overlaps, Echo Guard speaker state, and local audio artifacts. It writes
only under `derived/audit/group-overlaps/` and does not change `transcript.md`, `transcript.shadow_v2.md`,
Echo Guard outputs, synthesis notes, or `quality_verdict.json`.

Interpretation:

- `harmful.seconds`: likely duplicate `Me` text, remote leakage, or unsupported ASR noise.
- `benign_or_expected.seconds`: likely real double-talk or harmless boundary timing overlap.
- `review.seconds`: mixed or low-confidence regions that still need listening or future algorithmic
  improvement.
- `recommended_verdict_adjustment`: informational only; it does not rewrite the synthesis verdict.

When `--write-clips` is used, the script creates mono and stereo clips under
`derived/audit/group-overlaps/clips/`. `group_overlap_review.md` includes ready `afplay` commands
for the highest-priority examples.

## Audit-Informed Cleanup

When the group audit shows high-confidence duplicate `Me` utterances or short unsupported mic ASR
noise, apply the conservative cleanup profile:

```bash
.venv/bin/python scripts/apply-audit-cleanup.py "$SESSION" \
  --input-profile shadow_v2 \
  --output-profile audit_cleanup_v1 \
  --mode conservative

jq '.summary, .gates' \
  "$SESSION/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json"
```

This stage writes a new transcript profile and leaves `current` and `shadow_v2` untouched. It only
drops whole `Me` utterances when the group-overlap audit and local safety checks agree. It keeps
double-talk, timing overlap, remote leak, and human-review cases, but adds audit flags to affected
utterances.

Then build synthesis from the cleanup profile:

```bash
.venv/bin/python scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile audit_cleanup_v1

jq '{verdict, selected_transcript_profile, metrics, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.json"

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v1.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.audit_cleanup_v1.md"
```

`--transcript-profile auto` also chooses `audit_cleanup_v1` when the cleanup report exists and its
gates pass. Use the explicit profile when comparing runs.

## Audio Review Pack

For agent-driven review, build a local pack of suspicious clips after synthesis:

```bash
.venv/bin/python scripts/build-audio-review-pack.py "$SESSION" \
  --profile audit_cleanup_v1 \
  --write-clips \
  --max-items 160

.venv/bin/python scripts/audit-audio-review-pack.py "$SESSION"

jq '{items, likely_reliable, probable_error, needs_stronger_audio_judge, recommended_next_step}' \
  "$SESSION/derived/audit/audio-review-pack/audio_review_summary.json"

less "$SESSION/derived/audit/audio-review-pack/audio_review_report.md"
```

The pack is written under `derived/audit/audio-review-pack/`. It includes short `mic_raw`,
`remote`, `mic_clean`, `mic_role_masked` and stereo comparison clips for suspicious transcript
regions. The local audit classifies each item as likely reliable, probable transcript error, or
needing a stronger local audio judge. It does not rewrite transcripts, Echo Guard outputs,
synthesis files or raw `audio/*.caf`.
Low-risk `likely_reliable` items can have confidence below `0.70` when local metrics support the mic
utterance and competing error classes are clearly weaker. Treat this as review-priority reduction,
not as proof that the transcript is perfect.

## Audit Cleanup v2

After audio review, build a second cleanup profile:

```bash
.venv/bin/python scripts/apply-audit-cleanup.py "$SESSION" \
  --input-profile audit_cleanup_v1 \
  --output-profile audit_cleanup_v2 \
  --mode conservative

.venv/bin/python scripts/synthesize-simple-extractive.py "$SESSION" \
  --transcript-profile audit_cleanup_v2

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v2.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v2.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.audit_cleanup_v2.md"
```

`audit_cleanup_v2` keeps v1 intact and reads `audio_review_audit.jsonl` as extra evidence. It only
drops whole `Me` utterances when audio review marks them as high-confidence `remote_duplicate` or
short `asr_noise`. `remote_leak`, `lost_me`, `uncertain`, `double_talk` and `timing_overlap` are
kept and marked.

## Audit Cleanup v3

After building the regression corpus and audio judge, v3 can consume high-confidence queue
predictions:

```bash
.venv/bin/python scripts/apply-audit-cleanup.py "$SESSION" \
  --input-profile audit_cleanup_v2 \
  --output-profile audit_cleanup_v3 \
  --mode conservative \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

.venv/bin/python scripts/synthesize-simple-extractive.py "$SESSION" \
  --transcript-profile audit_cleanup_v3
```

`audit_cleanup_v3` is intentionally narrow. It uses the audio judge only as extra evidence over the
existing audio-review record. A whole `Me` utterance can be dropped only when the judge predicts
`drop_error` with high confidence and the original duplicate/noise safety gates still pass.
`mark_only_error`, `uncertain`, `remote_leak`, `lost_me`, double-talk and timing overlap remain
review/mark-only cases. If v3 applies no patches, automatic profile selection keeps using v2 or v1.

## Audit Cleanup v4

v4 is a narrow follow-up over v3. It keeps the same audio-judge queue, but allows a lower
`drop_error` confidence threshold only for strong remote duplicates:

```bash
.venv/bin/python scripts/apply-audit-cleanup.py "$SESSION" \
  --input-profile audit_cleanup_v3 \
  --output-profile audit_cleanup_v4 \
  --mode conservative \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

.venv/bin/python scripts/synthesize-simple-extractive.py "$SESSION" \
  --transcript-profile audit_cleanup_v4
```

The expanded v4 gate still requires strong text containment, low local support, good overlap
coverage, at most two unique `Me` content tokens, no protected action/decision/risk marker, no
intentional repeat and no notes impact. It does not drop `remote_leak`, `lost_me`, `uncertain`,
double-talk or timing-overlap cases.

## Session Quality Report

For a regression set or a batch of real meetings, build a private quality summary from existing
derived artifacts:

```bash
.venv/bin/python scripts/report-session-quality.py \
  sessions/<session-1> \
  sessions/<session-2> \
  --write-session-readiness

less sessions/_reports/session-quality/session_quality_report.md
```

The report reads `quality_report*.json`, Echo Guard `local_fir_report.json`, local recall audit, group overlap audit,
audit cleanup, synthesis verdicts, evidence note counts and audio review audit summaries. It writes
JSON, CSV and Markdown under `sessions/_reports/session-quality/` by default, which is ignored
together with `sessions/`. It does not run ASR, does not rewrite transcripts and does not touch raw
`audio/mic/*.caf` or `audio/remote/*.caf`.

With `--write-session-readiness`, the script also writes
`SESSION/derived/readiness/session_readiness.json` and `.md` for each input session. This is the
short per-meeting use gate for day-to-day work.

Audio-review metrics in this report are profile-aware. The script reads the selected
`clean_dialogue*.json` profile and excludes audio-review items whose `Me` utterance has already been
removed by cleanup. The raw audio-review totals remain useful for debugging, but the operational
review burden should follow the adjusted counters.

Audio-review classification is conservative about errors but does not over-escalate benign ties.
If the strongest explanations are all benign, for example timing overlap versus double-talk versus
reliable local speech, and no error class is strong, the item is treated as `likely_reliable` instead
of `needs_stronger_audio_judge`.

Local-recall metrics are also audited. If `local_only_island_recall < 0.9` but the audit finds only
short or weak unrecovered islands, the warning stays visible in `local_recall_review.md` and no
longer blocks `ready_for_notes`. Possible lost `Me` speech still keeps the session in `review_first`.
Blocking local-recall items are added to the operational review queue with short `ffplay` commands
against the mic capture.

## Regression Corpus

Use the already-audited sessions to build a private corpus for future cleanup and local audio-judge
work:

```bash
.venv/bin/python scripts/build-regression-corpus.py \
  sessions/<session-1> \
  sessions/<session-2> \
  --per-label 16 \
  --max-items 160

.venv/bin/python scripts/evaluate-regression-corpus.py \
  --corpus-dir sessions/_reports/regression-corpus

.venv/bin/python scripts/train-audio-judge-v0.py \
  --corpus-dir sessions/_reports/regression-corpus \
  --out-dir sessions/_reports/audio-judge-v0

.venv/bin/python scripts/report-operational-readiness.py \
  --session-quality sessions/_reports/session-quality/session_quality_report.json \
  --corpus-evaluation sessions/_reports/regression-corpus/regression_corpus_evaluation.json \
  --audio-judge sessions/_reports/audio-judge-v0/audio_judge_v0_report.json \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

.venv/bin/python scripts/build-review-plan.py \
  --operational-readiness sessions/_reports/operational-readiness/operational_readiness_report.json

.venv/bin/python scripts/review-decisions-cli.py \
  --template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --out sessions/_reports/review-plan/review_decisions.jsonl

.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --synthesize

less sessions/_reports/regression-corpus/regression_corpus.md
less sessions/_reports/regression-corpus/regression_corpus_evaluation.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_report.md
less sessions/_reports/operational-readiness/operational_readiness_report.md
less sessions/_reports/review-plan/review_plan.md
```

The script reads `derived/audit/audio-review-pack/audio_review_audit.jsonl`, balances examples by
label, and copies existing review clips under `sessions/_reports/regression-corpus/clips/`. It is
audit-only: no transcript profile, Echo Guard artifact or raw `audio/*.caf` file is modified.
The evaluation script labels the corpus readiness for future work: silver cleanup positives,
silver keep negatives, mark-only regressions, and examples that need a stronger local audio judge.
The audio judge v0 script trains a local shadow classifier on those silver labels using numeric
audio/text metrics only. It reports leave-one-session-out validation and predictions, but never edits
transcripts. Queue predictions are conservative: `drop_error` and `mark_only_error` are candidates
for cleanup/review work, not automatic review-burden reduction. `audit_cleanup_v3` consumes only
high-confidence `drop_error` predictions that also pass the conservative cleanup gates. v4 can
consume a few more strong duplicate predictions, but remains mark-only for leak/uncertain classes.
The operational readiness report answers whether the current pipeline is usable for medium-risk
working meetings, how much manual review remains, which sessions are `ready_for_notes` versus
`review_first`, and which audio-review clips should be checked first. Its review queue is also
profile-aware: already-resolved cleanup items are not shown as remaining work.
Stale audio-judge queue rows are also ignored when the current audio-review audit has reclassified
the source item as reliable.
Its `promotion_plan` section explains the current delta to `medium_risk_ready`: unresolved warnings,
sessions not ready for notes, remaining review minutes, and the next action class.
`build-review-plan.py` turns that queue into a compact checklist. Use it when a session is
`review_first`: listen to the listed stereo clips or local-recall mic snippets, decide whether each
`Me` candidate is leaked remote speech, real local speech, lost local speech, or unclear, then keep
unclear cases marked for review.
The plan also assigns each row a `review_lane`: `fast_confirm_drop` for likely duplicate/noise rows,
`check_unique_me_content` for partial duplicates and leaks, `check_local_recall` for possible missing
local speech, `confirm_benign` for likely harmless overlap, and `classify_audio` for anything else.
Close `fast_confirm_drop` first; it gives the largest cleanup with the least judgment. Keep the
other lanes conservative unless the audio is clear.
`review-decisions-cli.py` is the normal way to fill the checklist: it plays each preferred clip,
shows the transcript rows, respects each row's `allowed_decisions`, and writes
`review_decisions.jsonl` after every answer. It also prints nearby turns from the reviewed transcript
profile. Keep `--context-utterances 2` unless you need a shorter terminal view; use
`--context-utterances 0` to hide context. When several clips are available, the CLI prints
`audio=1:stereo_clean_left_remote_right, 2:stereo_mic_left_remote_right, ...`; type the number to
play that exact clip, or `p` to replay the preferred clip. At exit, the CLI prints review progress;
when no `todo` rows remain it prints the batch command that applies decisions and refreshes
readiness reports.
To close only one lane, pass `--lane`, for example:

```bash
.venv/bin/python scripts/review-decisions-cli.py \
  --template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --out sessions/_reports/review-plan/review_decisions.jsonl \
  --lane fast_confirm_drop
```

If the lane contains many short clips, build a single listening pack first:

```bash
.venv/bin/python scripts/build-review-lane-pack.py \
  --template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --lane fast_confirm_drop

afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav
less sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md
```

Then copy decisions from the pack back into the full review file:

```bash
.venv/bin/python scripts/apply-review-lane-pack-decisions.py \
  sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json \
  --answers dddddddddd \
  --out sessions/_reports/review-plan/review_decisions.jsonl
```

Answer shortcuts are `d=drop_me`, `k=keep_me`, `r` or `?=needs_review`, `s=skip`, and `.` or `n=todo`.

`apply-review-decisions-batch.py` is the normal command after the review file is filled. It applies
the same edited JSONL to every session mentioned in the review plan and can immediately regenerate
extractive notes. Under the hood, `apply-review-decisions.py` writes a separate `reviewed_v1`
profile for each session. It can drop whole reviewed `Me` utterances, clear review flags for
confirmed local speech, close checked local-recall rows, or keep an item marked `needs_review`.
Local-recall rows are audit-only: they do not add missing words to the transcript, and `drop_me` is
not a valid decision for them. `reviewed_v1` gates pass only when
the corresponding `review_decisions.template.jsonl` rows for that session are all closed with
an allowed `drop_me`, `keep_me`, `needs_review`, or `skip`; a partial or invalid file is written for
audit but is not selected by `auto`. The template includes `suggested_decision` hints, but they are
not applied until the reviewer explicitly edits `decision`. For `remote_duplicate`, those hints are coverage-aware:
if the duplicate covers only part of a longer `Me` utterance, the plan uses
`check_unique_me_content` and suggests `needs_review`, because `drop_me` would remove the whole
utterance. It does not edit `audit_cleanup_v1/v2/v3/v4`.

After closing a review file, prefer:

```bash
.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --synthesize \
  --refresh-reports
```

`--refresh-reports` reruns extractive notes for reviewed sessions and then refreshes
`session-quality`, `operational-readiness`, and the next `review-plan`. If an existing
`session_quality_report.json` is present, the refresh keeps its full session set instead of
shrinking the global report to only the sessions touched by the review file.

## Outputs

```text
derived/
  asr/
    mic.wav
    remote.wav

  transcript-simple/
    whisper-cpp/
      raw/
        chunks/
        mic.txt
        mic.json
        mic.vtt
        remote.txt
        remote.json
        remote.vtt
      prepared-audio/
        mic_speech.wav
        remote_loudnorm.wav
      resolved/
        raw_segments.json
        candidate_utterances.json
        role_decisions.json
        clean_dialogue.json
        overlaps.json
        quality_report.json
        timeline_repair_report.json
        timeline_repair_examples.jsonl
        timeline_audit_examples.jsonl
        clean_dialogue.shadow_v2.json
        quality_report.shadow_v2.json
        timeline_repair_report.shadow_v2.json
        timeline_repair_examples.shadow_v2.jsonl
        timeline_audit_examples.shadow_v2.jsonl
        opening_repair_report.shadow_v2.json
        opening_candidates.shadow_v2.jsonl
        opening_micro_asr_runs.shadow_v2.jsonl
        opening_patch.shadow_v2.json
        corrections.shadow_v2.jsonl
        transcribe_simple_report.shadow_v2.json
        transcript.shadow_v2.md
        repair_comparison.json
        transcript.simple.json
        transcript.md
        corrections.jsonl
        transcribe_simple_report.json
        clean_dialogue.audit_cleanup_v1.json
        quality_report.audit_cleanup_v1.json
        overlaps.audit_cleanup_v1.json
        transcript.simple.audit_cleanup_v1.json
        transcript.audit_cleanup_v1.md
      timeline-audit/
        current/
        shadow_v2/
      audit-cleanup/
        audit_cleanup_report.audit_cleanup_v1.json
        audit_cleanup_patches.audit_cleanup_v1.jsonl
        audit_cleanup_rejected_patches.audit_cleanup_v1.jsonl
        audit_cleanup_diff.audit_cleanup_v1.json

  synthesis-simple/
    extractive/
      synthesis_manifest.json
      quality_verdict.json
      quality_verdict.md
      notes.md
      evidence_notes.json
      review_items.jsonl
      quality_verdict.audit_cleanup_v1.json
      quality_verdict.audit_cleanup_v1.md
      notes.audit_cleanup_v1.md
      evidence_notes.audit_cleanup_v1.json

  audit/
    group-overlaps/
      group_overlap_audit.jsonl
      group_overlap_summary.json
      group_overlap_review.md
      group_overlap_patch_suggestions.jsonl
      clips/
```

Initial role assignment is deliberately simple:

```text
mic.wav    -> Me
remote.wav -> Colleagues
```

No remote diarization is attempted.

The bridge then runs a small reconciliation layer:

```text
raw_segments.json
  -> candidate_utterances.json
  -> timeline repair
  -> role_decisions.json
  -> clean_dialogue.json
  -> transcript.md
```

For `mic` candidates it combines Echo Guard speaker-state features, time overlap with `remote`
candidates, normalized text similarity, and a small domain-term normalizer. Remote candidates remain
authoritative for `Colleagues`.

Timeline repair runs before role decisions. If whisper.cpp glues a long `Me` segment across a
remote reply, the bridge treats the remote intervals as authoritative, cuts the mic candidate around
guarded remote spans, and keeps only local islands from `speaker_state.jsonl`. Short local islands
are re-recognized from `mic_clean_local_fir.wav` with micro ASR; longer islands may use token
timestamps as fallback. If no reliable local island remains, the original long mic candidate is
dropped instead of being published as one misleading `Me` block.

Repair actions are explicit in `role_decisions.json`:

```text
keep
drop
split
micro_reasr
keep_needs_review
```

`timeline_repair_report.json` contains aggregate counters. `timeline_repair_examples.jsonl` records
the parent candidate, matched remote intervals, local islands, and children for every repaired or
dropped crossing.

`shadow_v2` keeps the same baseline transcript intact, then runs a more expensive micro-ASR pass for
short local islands. It tries `mic_clean_local_fir.wav`, `mic_raw_for_asr.wav`, and
`mic_role_masked_for_asr.wav` when they exist, adds a short leading silence to temporary micro-ASR
clips, and tries normal/wide recognition windows. The already accepted `current` micro-ASR result is
used as the baseline candidate: a new decode can replace it only when it wins by score and does not
drop the beginning of the baseline phrase. This keeps the current transcript stable while testing
more aggressive recovery.

`shadow_v2` also has a deliberately narrow boundary-prefix repair for short local utterances. The
first supported case is `адно` -> `Ладно` near the start of an utterance, only when the local score is
high, remote similarity is low, and nearby words support the correction. Such edits are written to
`corrections.shadow_v2.jsonl` and to `timeline_audit_examples.shadow_v2.jsonl` as
`boundary_repair_candidate`; they are not applied to the default `transcript.md`.

`shadow_v2` also runs a guarded start-of-call repair when the first remote segment starts too early
or the first local-only mic island is missing from the dialogue. It uses short micro-ASR windows over
remote and mic sources to recover only a small lexicon of opening turns such as `Привет`,
`Меня слышно?`, `Привет, да`, then retimes the first long remote content segment after those turns.
The patch is applied only if `opening_gate_passed` is true; otherwise the proposal remains in
`opening_*shadow_v2*` audit files and the shadow transcript stays unchanged.

`repair_comparison.json` compares current and shadow quality metrics and applies no-regression
gates. Session-specific golden checks should live in private fixtures or local configuration, not in
the open-source defaults. A failing gate means the shadow transcript is useful for review but should
not become the default yet.

The bridge also uses token-level confidence from whisper.cpp full JSON:

- short, low-confidence remote segments are dropped as probable ASR noise;
- remote display timestamps use the first confident speech token when Whisper's broad segment offset
  starts too early;
- long mic segments are split on large internal token gaps before role reconciliation, because
  whisper.cpp can glue separate local remarks into one segment across a pause or remote reply;
- timeline repair splits long mic candidates around authoritative remote intervals and can run
  micro-ASR for short local islands;
- a mic segment pinned to the beginning of the file can use Echo Guard's local-support start as the
  display timestamp;
- if a low-confidence remote candidate has matching mic candidates that were dropped as remote
  leakage, the remote utterance may use that mic duplicate text while keeping the `Colleagues` role.
  In JSON this is marked as `quality.text_source = matched_mic_echo_duplicate`.

To debug raw mic recognition without this reconciliation filter:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --skip-export \
  --skip-transcribe \
  --mic-policy keep_all
```

Dropped raw segments and dropped candidates are written to `resolved/corrections.jsonl`. Aggregate
counts are in `resolved/transcribe_simple_report.json` and `resolved/quality_report.json`.
Cross-role overlaps are written to `resolved/overlaps.json`.
Timeline repair details are in `resolved/timeline_repair_report.json` and
`resolved/timeline_repair_examples.jsonl`.

Risky remaining places are collected in `resolved/timeline_audit_examples.jsonl`: every
`needs_review` utterance and every cross-role overlap longer than two seconds. Each row contains the
utterance data, overlap metadata when present, nearby Echo Guard speaker-state rows, and `afplay`
commands for short mic/remote clips cut under `derived/transcript-simple/whisper-cpp/timeline-audit/`.
Raw `audio/*.caf` files are not touched.

## Extractive Synthesis

`scripts/synthesize-simple-extractive.py` reads only derived transcript JSON. It does not run ASR,
does not call an LLM, and does not read raw audio.

Profile selection:

```text
auto      -> reviewed_v1 when review gates pass, then audit_cleanup_v4/v3/v2/v1 with passing cleanup gates, then shadow_v2 if repair_comparison.json passes, otherwise current
current   -> baseline clean_dialogue.json
shadow_v2 -> shadow clean_dialogue.shadow_v2.json, marked risky if comparison failed
audit_cleanup_v1..v4 -> audit-cleaned dialogue, marked risky if cleanup gates failed
reviewed_v1 -> human-reviewed dialogue, marked risky if review gates failed
```

The script writes a quality verdict and a conservative `notes.md`. The v3 notes path is extractive
and scored:

```text
clean_dialogue.json
  -> topic_blocks
  -> candidate_items
  -> scored_items
  -> selected_notes
```

Markdown shows only selected top items. `evidence_notes.json` keeps all candidates, including weak
actions, process discussions and meeting facilitation hidden from Markdown. The script does not
invent owners, deadlines or decisions.

Useful checks:

```bash
jq '{schema, metrics, selected_counts: .metrics.selected_counts}' \
  "$SESSION/derived/synthesis-simple/extractive/evidence_notes.json"

jq -r '.candidates[] | select(.type == "action") | [.subtype, .status, .score, .display_text] | @tsv' \
  "$SESSION/derived/synthesis-simple/extractive/evidence_notes.json" | head

less "$SESSION/derived/synthesis-simple/extractive/notes.audit_cleanup_v1.md"
```

## Reusing Existing Raw Whisper Output

If `raw/mic.json` and `raw/remote.json` already exist, rebuild only the resolved artifacts:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" --skip-export --skip-transcribe
```

## Debugging Short Runs

Limit Whisper to the first 20 seconds:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --duration-ms 20000 \
  --force
```

Regenerate the full transcript with the selected model:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --force
```

## Known Limitations

- The transcript is a draft.
- `whisper.cpp` may hallucinate repeated short phrases on long silence.
- The script filters obvious subtitle-credit hallucinations, repeated goodbye tails, weak isolated short phrases, and likely remote leakage in mic.
- Reconciliation is rule-based and still conservative; inspect `role_decisions.json` and `overlaps.json` for hard cases.
- Track roles are only first-level roles. `Colleagues` is not diarized into individual people.
- The bridge can pass a compact domain prompt to `whisper.cpp`, but it still does not perform
  glossary-based post-correction or semantic correction.
