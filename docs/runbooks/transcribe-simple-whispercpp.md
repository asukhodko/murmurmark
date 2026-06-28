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

Install the local CLI wrapper once:

```bash
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"
murmurmark doctor
```

Start from a completed session and run the current full post-recording pipeline:

```bash
SESSION=./sessions/<session>

murmurmark config print
murmurmark process "$SESSION"

murmurmark report "$SESSION"
less "$SESSION/derived/readiness/session_readiness.md"
murmurmark retention plan "$SESSION"
```

Copy `murmurmark.config.example.json` to `murmurmark.config.json` when you want local defaults for
model, language, prompt and export. Explicit command-line flags override config values.
`murmurmark doctor` reports the selected config and model path, so run it after changing local
defaults.

`murmurmark process` calls the current runner: Echo Guard, export/transcription, shadow timeline repair,
local-recall audit, transcript-order audit, group-overlap audit, audio-review audit,
`audit_cleanup_v1..v4`, extractive synthesis and per-session readiness. `audit_cleanup_v5` is a separate batch step after the suggested-review shadow report. Use
`--force-asr` when you need to regenerate Whisper output, and `--reuse-asr-cache` when you only want
to rebuild repair, cleanup, synthesis and reports from cached ASR JSON.

For the usual record-then-process flow:

```bash
murmurmark record --target-bundle system
murmurmark process latest
murmurmark report latest
murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
```

After a successful recording, `record` prints `SESSION="..."` and the exact `murmurmark process ...`
command for that session. `process latest` remains a convenient shortcut when the newest session is
the one you just recorded.

Read `derived/readiness/session_readiness.md` before using a meeting result. It contains:

- `ready_for_notes`: the notes can be used with normal caution;
- `review_first`: check the listed audio-review regions before using the result for medium-risk work;
- `do_not_use_without_manual_review`: the transcript is too risky for unattended use;
- `pipeline_incomplete`: rerun the full pipeline before judging the session.

It also contains `Next Commands`: the shortest CLI path from the current state, such as rerunning
`murmurmark process`, building the first recommended review lane, exporting Markdown, or planning
retention. For `review_first`, the command chain ends with `murmurmark review apply` after the
workspace answers have been closed.

`murmurmark report SESSION` prints the same selected profile, verdict, review burden and synthesis
review item summary, so the terminal output is enough to see whether notes still depend on risky
utterances before opening the Markdown files.
Use `murmurmark review next SESSION` when you only need the next review step for one session. It
refreshes session readiness, builds a session-local review plan under
`SESSION/derived/readiness/review-plan/` when review is needed, and prints the review-oriented
command chain. Pass `--no-refresh` to read an already current `session_readiness.json`, or
`--no-plan` to refresh readiness without rebuilding the session-local plan.

After export, keep a retention plan with the session:

```bash
murmurmark export "$SESSION" --format markdown --include-json
murmurmark retention plan "$SESSION"
murmurmark retention payload "$SESSION"
less "$SESSION/derived/retention/retention_plan.json"
less "$SESSION/derived/retention/provider_payload_manifest.json"
```

The CLI prints a compact summary after each retention command, including action counts,
blockers/warnings and the next safe command. The JSON files remain the source of truth.

The default policy keeps raw audio and records that raw audio is not copied into export bundles.
Destructive raw deletion requires an explicit policy, `retention apply`, a successful export manifest,
and `--confirm-delete-raw`.
The default provider payload manifest is blocked because external providers are disabled by the
local-first policy. This is expected.

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

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
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

For domain-specific calls, pass a compact local prompt file:

```bash
ASR_PROMPT="domain-packs/<domain>/whisper-prompt.ru.txt"
scripts/transcribe-simple-whispercpp.py "$SESSION" --model "$MODEL" --language ru --prompt-file "$ASR_PROMPT"
```

The prompt is derived from a local domain pack. It is intentionally short because `whisper.cpp` uses
it as ASR context, not as a post-processing dictionary.

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
murmurmark synthesize "$SESSION" --transcript-profile auto

jq '{verdict, selected_transcript_profile, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark notes "$SESSION" --path-only
murmurmark transcript "$SESSION"
less "$(murmurmark transcript "$SESSION" --path-only)"
```

## Local Recall Audit

Timeline repair can report low `local_only_island_recall` when a long `Me` candidate is split around
remote speech and some short local-only islands are not recovered as separate `Me` utterances. Run
the local recall audit to separate likely harmless short/weak islands from possible lost local
speech:

```bash
murmurmark audit local-recall "$SESSION" --profile shadow_v2

jq '.summary' "$SESSION/derived/audit/local-recall/local_recall_audit.json"
less "$SESSION/derived/audit/local-recall/local_recall_review.md"
```

The CLI prints a compact summary with missing-island counts, possible lost local speech,
review seconds and the next report to inspect.

The audit reads only timeline-repair examples and Echo Guard `speaker_state.jsonl`. It writes under
`derived/audit/local-recall/`, does not edit transcripts, and is used by
`report-session-quality.py` to decide whether low local recall should block `ready_for_notes`.
If an unrecovered local island is already covered by nearby remote transcript text, the audit labels
it `likely_harmless_remote_covered`: content is preserved, and the remaining risk is attribution,
not missing meeting substance. Short islands on parent, recovered-child or remote-guard boundaries
are labelled `likely_harmless_boundary_fragment`; they stay visible in audit JSON but should not
inflate the review queue unless other local-speech evidence is strong. Short acknowledgement-only
islands such as `понял` or `окей` are labelled `likely_harmless_ack_fragment`; they remain visible
for audit, but do not become blocking lost-speech evidence by themselves.

## Transcript Order Audit

Wrong order is usually not a global sort problem. It appears when a long `Me` utterance crosses a
remote reply and continues after it, so the final Markdown can show a local reaction before the
remote phrase that caused it. Run the order audit to find those regions explicitly:

```bash
murmurmark audit order "$SESSION" --profile auto

jq '.summary' "$SESSION/derived/audit/order/transcript_order_audit.json"
less "$SESSION/derived/audit/order/transcript_order_review.md"
```

The audit reads `clean_dialogue` and `overlaps`, writes under `derived/audit/order/`, and never edits
transcript profiles. `probable_order_risk` means a long `Me` turn wraps a `Colleagues` turn and has a
tail after it; this is included in session readiness review burden and should go to review before
using the transcript for precise chronology.

For the narrow safe case where the long `Me` turn can be split by saved source ASR segments, build an
explicit repair profile:

```bash
murmurmark repair order "$SESSION" \
  --input-profile auto \
  --output-profile order_repair_v1

murmurmark synthesize "$SESSION" --transcript-profile order_repair_v1
less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.order_repair_v1.md"
jq '.summary, .gates' \
  "$SESSION/derived/transcript-simple/whisper-cpp/order-repair/transcript_order_repair_report.order_repair_v1.json"
```

`order_repair_v1` does not rewrite baseline, `shadow_v2`, cleanup or reviewed profiles. It only
replaces one risky `Me` utterance with before/after `Me` utterances when the source mic ASR segments
sit cleanly around the remote turn and the dropped overlap text is covered by the remote transcript.
Otherwise it keeps the original utterance and marks `quality.transcript_order_repair.status =
needs_review`.
After a passing repair, `murmurmark report "$SESSION"` can select `order_repair_v1` and show
`transcript_order_repaired_clear` instead of keeping the old order-risk burden. Partial repair is
also valid: already-split regions are used, but unrepaired regions stay as `needs_review` and remain
in `transcript_order_review_seconds`.

For corpus work, aggregate order risks after the usual session-quality report:

```bash
murmurmark corpus order
murmurmark corpus order --repair

jq '.summary' sessions/_reports/transcript-order/transcript_order_corpus_report.json
less sessions/_reports/transcript-order/transcript_order_corpus_report.md
```

Without `--repair`, the command only aggregates existing order audits. With `--repair`, it uses the
sessions from the current session-quality report, refreshes their order audit, writes conservative
`order_repair_v1`, refreshes session-quality and then rebuilds the corpus order report. Pass explicit
session paths or `all` when you need a different target set. This report is the practical list of
chronology regression candidates.
Complete sessions with
blocking order risk fail `check-corpus-gates.py` through `transcript.no_blocking_order_risk`; partial
historical sessions remain visible as review material without blocking the complete-session gate.
`check-corpus-gates.py` also reads the aggregate report itself and fails
`transcript_order.no_complete_blocking_sessions` if any complete session remains blocked by
chronology risk.

## Remote Leak Segment Plan

Remote leak needs a different safety shape. A `remote_leak` region can still contain real local
content from `Me`, so the next safe step is an audit-only segment plan:

```bash
murmurmark repair remote-leak "$SESSION"

less "$SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md"
jq '.summary, .action_plan, .policy' \
  "$SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
```

The planner reads `derived/audit/audio-review-pack/audio_review_audit.jsonl`, selects
`remote_leak` rows with `probable_transcript_error`, and writes only
`remote_leak_segment_repair_*` artifacts. It does not edit transcript profiles, Echo Guard outputs,
or raw CAF files. Items with unique local `Me` content are labelled
`remote_leak_with_local_content_risk`; those are future segment-level repair candidates, not
whole-utterance drops.

## Group Overlap Audit

For group calls, `remote_duplicate_in_me_seconds` can overstate the real damage. Some overlap is
normal double-talk, and some is only timestamp boundary overlap between adjacent turns. Run the
group audit after transcription and synthesis when you need to separate harmful duplicates from
expected group-call overlap:

```bash
murmurmark audit group-overlaps "$SESSION" \
  --profile shadow_v2 \
  --min-overlap-sec 0.5 \
  --review-threshold-sec 2.0 \
  --write-clips \
  --max-clips 80

jq '{classified, harmful, benign_or_expected, review, recommended_verdict_adjustment}' \
  "$SESSION/derived/audit/group-overlaps/group_overlap_summary.json"

less "$SESSION/derived/audit/group-overlaps/group_overlap_review.md"
```

The CLI summary shows total overlap, harmful seconds, benign/expected seconds, review seconds and
the informational verdict adjustment.

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
murmurmark cleanup "$SESSION" \
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
murmurmark synthesize "$SESSION" --transcript-profile audit_cleanup_v1

jq '{verdict, selected_transcript_profile, metrics, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.json"

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v1.md"
murmurmark notes "$SESSION" --profile audit_cleanup_v1 --kind verdict
murmurmark notes "$SESSION" --profile audit_cleanup_v1
```

`--transcript-profile auto` also chooses `audit_cleanup_v1` when the cleanup report exists and its
gates pass. Use the explicit profile when comparing runs.

## Audio Review Pack

For agent-driven review, build a local pack of suspicious clips after synthesis:

```bash
murmurmark audit audio-review "$SESSION" \
  --profile audit_cleanup_v1 \
  --write-clips \
  --max-items 160

jq '{items, likely_reliable, probable_error, needs_stronger_audio_judge, recommended_next_step}' \
  "$SESSION/derived/audit/audio-review-pack/audio_review_summary.json"

less "$SESSION/derived/audit/audio-review-pack/audio_review_report.md"
```

The CLI summary shows item counts, likely reliable seconds, probable transcript errors, stronger
audio-judge demand and the next report to inspect.

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
murmurmark cleanup "$SESSION" \
  --input-profile audit_cleanup_v1 \
  --output-profile audit_cleanup_v2 \
  --mode conservative

murmurmark synthesize "$SESSION" \
  --transcript-profile audit_cleanup_v2

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v2.md"
murmurmark notes "$SESSION" --profile audit_cleanup_v2 --kind verdict
murmurmark notes "$SESSION" --profile audit_cleanup_v2
```

`audit_cleanup_v2` keeps v1 intact and reads `audio_review_audit.jsonl` as extra evidence. It only
drops whole `Me` utterances when audio review marks them as high-confidence `remote_duplicate` or
short `asr_noise`. `remote_leak`, `lost_me`, `uncertain`, `double_talk` and `timing_overlap` are
kept and marked.

## Audit Cleanup v3

After building the regression corpus and audio judge, v3 can consume high-confidence queue
predictions:

```bash
murmurmark cleanup "$SESSION" \
  --input-profile audit_cleanup_v2 \
  --output-profile audit_cleanup_v3 \
  --mode conservative \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

murmurmark synthesize "$SESSION" \
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
murmurmark cleanup "$SESSION" \
  --input-profile audit_cleanup_v3 \
  --output-profile audit_cleanup_v4 \
  --mode conservative \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

murmurmark synthesize "$SESSION" \
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
audit cleanup, synthesis verdicts, synthesis `review_summary`, evidence note counts and audio review audit summaries. It writes
JSON, CSV and Markdown under `sessions/_reports/session-quality/` by default, which is ignored
together with `sessions/`. It does not run ASR, does not rewrite transcripts and does not touch raw
`audio/mic/*.caf` or `audio/remote/*.caf`.

With `--write-session-readiness`, the script also writes
`SESSION/derived/readiness/session_readiness.json` and `.md` for each input session. This is the
short per-meeting use gate for day-to-day work. The JSON includes `next_commands`; the Markdown view
prints the same commands under `Next Commands`.

Audio-review metrics in this report are profile-aware. The script reads the selected
`clean_dialogue*.json` profile and excludes audio-review items whose `Me` utterance has already been
removed by cleanup. The raw audio-review totals remain useful for debugging, but the operational
review burden should follow the adjusted counters.
Adjusted audio-review seconds are union durations per verdict. This avoids double-counting multiple
audit rows that point to the same suspicious utterance or overlapping interval.

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
.build/debug/murmurmark corpus build \
  sessions/<session-1> \
  sessions/<session-2> \
  --per-label 16 \
  --max-items 160

.build/debug/murmurmark corpus evaluate

.build/debug/murmurmark corpus train-audio-judge

.build/debug/murmurmark corpus taxonomy

.build/debug/murmurmark corpus gate
.build/debug/murmurmark corpus gate \
  --write-baseline sessions/_reports/corpus-gates/baseline.local.json
.build/debug/murmurmark corpus gate \
  --baseline sessions/_reports/corpus-gates/baseline.local.json

.build/debug/murmurmark review next latest
.build/debug/murmurmark review plan
.build/debug/murmurmark review first-lane
.build/debug/murmurmark review latest --lane fast_confirm_drop
.build/debug/murmurmark review progress
.build/debug/murmurmark review apply
.build/debug/murmurmark export sessions/<session> --format markdown --include-json

less sessions/_reports/regression-corpus/regression_corpus.md
less sessions/_reports/regression-corpus/regression_corpus_evaluation.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_report.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_cv_predictions.jsonl
less sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.md
less sessions/_reports/operational-readiness/operational_readiness_report.md
less sessions/_reports/review-plan/review_plan.md
```

For a full refresh with all sessions under `./sessions`, use:

```bash
.build/debug/murmurmark corpus process all --per-label 16 --max-items 160
```

`corpus gate` reads the generated session-quality, regression-corpus, audio-judge and operational
readiness JSON reports, then writes `sessions/_reports/corpus-gates/corpus_gates_report.json` and
`.md`. `passed` or `passed_with_warnings` exits successfully. `failed` exits non-zero unless
`--no-fail` is used.

For risky algorithm changes, first save a private local baseline:

```bash
.build/debug/murmurmark corpus gate \
  --write-baseline sessions/_reports/corpus-gates/baseline.local.json
```

Then compare future corpus refreshes with it:

```bash
.build/debug/murmurmark corpus gate \
  --baseline sessions/_reports/corpus-gates/baseline.local.json
```

The baseline check catches drops in complete or `ready_for_notes` sessions, growth in `review_first`,
review burden increases, audio judge training/accuracy regressions, lost baseline sessions and
per-session drops in local recall or use gate. The baseline file lives under ignored generated
reports and must not be committed when it is built from real meetings.

`murmurmark export` is the user-facing handoff. It reads the selected transcript profile from
per-session readiness, copies the Markdown verdict, notes and transcript into `exports/private/`,
and writes `export_manifest.json`. It blocks sessions with readiness `export_blockers` by default;
use `murmurmark review plan` first when review is required, or pass `--force` only for debugging.
After a successful export, the CLI prints the manifest path, key output files and the retention
commands that should use the same manifest.

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
`murmurmark corpus taxonomy` then combines the corpus, evaluation buckets, audio-judge predictions
and session-quality state into one action map. Read it before changing cleanup or repair rules: it
separates safe cleanup evidence, mark-only errors, uncertain regions, benign overlap guards and
model/confidence gaps. It also writes diagnostic subtypes such as `uncertain_duplicate_vs_leak` and
`remote_leak_with_local_content_risk`; use those subtypes to choose the next narrow repair instead
of retuning all audio-review labels at once. Its `action_plan` section is the shortest handoff for
the next agent: it names the diagnostic subtype, the next work item and the expected deliverable.
The operational readiness report answers whether the current pipeline is usable for medium-risk
working meetings, how much manual review remains, which sessions are `ready_for_notes` versus
`review_first`, and which audio-review clips should be checked first. Its review queue is also
profile-aware: already-resolved cleanup items are not shown as remaining work.
Stale audio-judge queue rows are also ignored when the current audio-review audit has reclassified
the source item as reliable.
`murmurmark corpus order` writes a separate chronology-risk corpus report under
`sessions/_reports/transcript-order/`; read it when wrong reply order is the concern rather than
audio leakage.
`murmurmark corpus report` prints the existing session-quality summary and, when the files already
exist, also prints short summaries for transcript-order, corpus-gates and operational-readiness. It
does not rebuild those reports; it is the quick “what is the current state?” command after a heavier
`murmurmark corpus process all`. Use `murmurmark report corpus` when only session-quality needs a
refresh.
Its `promotion_plan` section explains the current delta to `medium_risk_ready`: unresolved warnings,
sessions not ready for notes, remaining review minutes, and the next action class.
Its `Review Queue Strategy` section groups the remaining queue into lanes and shows the first useful
lane to close, usually `fast_confirm_drop`, plus the estimated queue that remains after that lane.
`build-review-plan.py` turns that queue into a compact checklist. Use it when a session is
`review_first`: listen to the listed stereo clips or local-recall mic snippets, decide whether each
`Me` candidate is leaked remote speech, real local speech, lost local speech, order-risk, or unclear, then keep
unclear cases marked for review.
The plan also assigns each row a `review_lane`: `fast_confirm_drop` for likely duplicate/noise rows,
`check_unique_me_content` for partial duplicates and leaks, `check_local_recall` for possible missing
local speech, `check_transcript_order` for chronology-risk rows, `confirm_benign` for likely harmless
overlap, and `classify_audio` for anything else.
Close the plan's `first_recommended_lane` first; it is usually `fast_confirm_drop`, but can change as
the review queue changes. Keep the other lanes conservative unless the audio or chronology evidence
is clear.
`murmurmark review next "$SESSION"` is the quickest entry point for one session: it refreshes
`session_readiness.json`, shows gate/profile/verdict/review burden, builds a session-local review
plan if needed, then prints the next review commands. `murmurmark review first-lane --session
"$SESSION"` now defaults to that session-local plan. The same is true for
`murmurmark review workspace --session "$SESSION"`, `murmurmark review workspace apply --session
"$SESSION"` and `murmurmark review apply --session "$SESSION"`. Use `murmurmark review plan` for the
global corpus queue.
`murmurmark review first-lane` refreshes the plan and builds the lane pack for that recommended lane.
With `--session`, its default paths are under `SESSION/derived/readiness/`; without `--session`, it
uses the global corpus queue under `sessions/_reports/`.
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
.build/debug/murmurmark review latest --lane fast_confirm_drop
```

The lower-level equivalent is:

```bash
.venv/bin/python scripts/review-decisions-cli.py \
  --template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --out sessions/_reports/review-plan/review_decisions.jsonl \
  --lane fast_confirm_drop
```

If the lane contains many short clips, build a single listening pack first:

```bash
.build/debug/murmurmark review workspace --session latest

afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav
less sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md
$EDITOR sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt
```

To prepare all remaining lanes at once:

```bash
.build/debug/murmurmark review workspace
less sessions/_reports/review-plan/review_workspace.md
```

Then edit the answer sheet for the lane. Dots mean "not reviewed yet"; replace only the items you
have actually checked:

```bash
$EDITOR sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt
```

Each lane also has a suggested answer sheet. It is useful for comparison after listening:

```bash
less sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.suggested.txt
```

Then copy decisions from the answer sheet back into the full review file:

```bash
.venv/bin/python scripts/apply-review-lane-pack-decisions.py \
  sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json \
  --answers-file sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt \
  --out sessions/_reports/review-plan/review_decisions.jsonl
```

If several lane answer sheets have been edited, apply the whole workspace instead:

```bash
.build/debug/murmurmark review workspace apply
```

To estimate what the generated suggestions would do without writing decisions:

```bash
.build/debug/murmurmark review workspace apply --answers-source suggested --dry-run
```

Dry-run still writes `review_workspace_apply_report.json`, so the CLI can print the same summary and
next command without changing `review_decisions.jsonl`.

To materialize those suggestions as a separate shadow transcript for comparison, write a suggested
decisions file and build `suggested_review_v1`:

```bash
.build/debug/murmurmark review workspace apply \
  --answers-source suggested \
  --out sessions/_reports/review-plan/review_decisions.suggested.jsonl

.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.suggested.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --output-profile suggested_review_v1 \
  --synthesize \
  --out sessions/_reports/review-plan/review_decisions_apply.suggested_review_v1.json

.venv/bin/python scripts/report-suggested-review-shadow.py
.venv/bin/python scripts/apply-suggested-cleanup.py
```

`suggested_review_v1` is not a reviewed profile. It is a shadow candidate generated from
`suggested_decision` hints, so `auto` must not select it. The report under
`sessions/_reports/suggested-review-shadow/` compares it with the currently selected profile and
marks sessions as `promising_shadow_candidate`,
`promising_cleanup_candidate_with_residual_review`, `low_gain_shadow_candidate`, or
`do_not_promote`.

`scripts/apply-suggested-cleanup.py` turns only safe shadow `drop_me` candidates into
`audit_cleanup_v5`. It reads the selected cleanup profile, does not copy `suggested_review_v1`
wholesale, and skips low-gain or unsafe sessions. `audit_cleanup_v5` is eligible for `auto` only
when its cleanup gates pass and at least one patch was applied.

After rebuilding an audio-review pack for `audit_cleanup_v5`, run one more conservative cleanup pass
when fresh audio-review rows expose additional safe duplicates:

```bash
murmurmark cleanup "$SESSION" \
  --input-profile audit_cleanup_v5 \
  --output-profile audit_cleanup_v6

murmurmark synthesize "$SESSION" --transcript-profile audit_cleanup_v6
```

`audit_cleanup_v6` reuses the same audio-review gates as v2 over the v5 transcript. It is not a
suggested-review profile and does not use audio-judge predictions.

The automatic agent-reviewed layer uses audio-review audit rows plus the audio-judge queue to close
only rows that are safe without listening. It writes `agent_reviewed_v1`; this profile is selected by
`auto` after `reviewed_v1` and before automatic cleanup profiles when its gates pass.

```bash
murmurmark review agent
```

Use it as a medium-risk automation layer, not as proof that every remaining questionable phrase is
correct. `drop_me` is limited to clear whole-utterance duplicates/noise. `keep_me` closes review
burden for strong local-support rows; unresolved rows remain in `review_first` sessions.

Answer shortcuts are `d=drop_me`, `k=keep_me`, `r` or `?=needs_review`, `s=skip`, and `.` or `n=todo`.
Before applying decisions to transcripts, check progress and validation:

```bash
.venv/bin/python scripts/report-review-decisions-progress.py \
  --decisions sessions/_reports/review-plan/review_decisions.jsonl

less sessions/_reports/review-plan/review_decisions_progress.md
```

`apply-review-decisions-batch.py` is the normal command after the review file is filled. It applies
the same edited JSONL to every session mentioned in the review plan and can immediately regenerate
extractive notes. Under the hood, `apply-review-decisions.py` writes a separate `reviewed_v1`
profile for each session. The CLI wrapper prints the next `murmurmark report ...` command after a
successful apply. It can drop whole reviewed `Me` utterances, clear review flags for
confirmed local speech, close checked local-recall rows, or keep an item marked `needs_review`.
Local-recall rows are audit-only: they do not add missing words to the transcript, and `drop_me` is
not a valid decision for them. Transcript-order rows are audit-only for timeline content: review can
clear or keep the chronology risk and records `quality.transcript_order_review` on affected
utterances, but it does not move utterances or edit text. `reviewed_v1` gates pass only when
the corresponding `review_decisions.template.jsonl` rows for that session are all closed with
an allowed `drop_me`, `keep_me`, `needs_review`, or `skip`; a partial or invalid file is written for
audit but is not selected by `auto`. The template includes `suggested_decision` hints, but they are
not applied until the reviewer explicitly edits `decision`. For `remote_duplicate`, those hints are coverage-aware:
if the duplicate covers only part of a longer `Me` utterance, the plan uses
`check_unique_me_content` and suggests `needs_review`, because `drop_me` would remove the whole
utterance. It does not edit `audit_cleanup_v1/v2/v3/v4/v5/v6`.

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

  export/
    # user-facing exports are written outside the session by default:
    # exports/private/<session-dir-name>/

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
auto      -> reviewed_v1 when review gates pass, then agent_reviewed_v1 when agent gates pass, then audit_cleanup_v6/v5/v4/v3/v2/v1 with passing cleanup gates, but may select order_repair_v1 over any of those bases when order repair gates pass and at least one repair was applied; then shadow_v2 if repair_comparison.json passes, otherwise current
current   -> baseline clean_dialogue.json
shadow_v2 -> shadow clean_dialogue.shadow_v2.json, marked risky if comparison failed
audit_cleanup_v1..v6 -> audit-cleaned dialogue, marked risky if cleanup gates failed
reviewed_v1 -> human-reviewed dialogue, marked risky if review gates failed
agent_reviewed_v1 -> agent-reviewed dialogue, marked risky if review gates failed
suggested_review_v1 -> machine-suggested review candidate, explicit only, never selected by auto
order_repair_v1 -> transcript-order repair candidate, eligible for auto only when built over the selected base profile, gates passed and at least one repair was applied; marked risky if requested explicitly and gates failed
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
invent owners, deadlines or decisions. If a candidate uses an utterance with unresolved review
metadata, such as `transcript_order_review:needs_review`, the source is written into candidate
features and the item is penalized before Markdown selection. The `murmurmark synthesize` and
`murmurmark notes` command summaries print the remaining `review_items` count and top review item
types, so this is visible without opening JSON.

Useful checks:

```bash
jq '{schema, metrics, selected_counts: .metrics.selected_counts}' \
  "$SESSION/derived/synthesis-simple/extractive/evidence_notes.json"

jq '.review_summary' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

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
