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
murmurmark config init
```

Start from a completed session and publish the first authoritative result:

```bash
SESSION=./sessions/<session>

murmurmark config print
murmurmark process "$SESSION"

murmurmark status "$SESSION"
less "$SESSION/derived/readiness/session_readiness.md"
murmurmark retention plan "$SESSION"
```

The normal command stops after the authoritative transcript, verdict and next action are ready.
Run optional heavier diagnostics separately when needed:

```bash
murmurmark enrich "$SESSION"
```

To retain the old one-command behavior, use `murmurmark process "$SESSION" --full`. Either phase
can be interrupted with `Ctrl-C` and resumed with the same command.

To inspect the measured handoff gate across explicit sessions:

```bash
scripts/report-authoritative-handoff-corpus.py \
  sessions/<session-1> \
  sessions/<session-2> \
  sessions/<session-3> \
  --require-raw-integrity \
  --require-passing-gates

less sessions/_reports/authoritative-handoff/authoritative_handoff_corpus_v1.md
```

The report requires a sequential `1/1` baseline, a bounded `2/2` cold run and a checkpoint-reuse
run for every session. It compares transcript SHA-256, selected profile and quality metrics in
addition to elapsed time.

If synthesis or readiness still reports review risk, the CLI handoff points to
`murmurmark review next "$SESSION"` before export. Export is advertised from synthesis only after a
good verdict with no review items.

Run `murmurmark config init` when you want local defaults for model, language, prompt and export.
Explicit command-line flags override config values.
`murmurmark doctor` reports the selected config and model path, so run it after changing local
defaults.

`murmurmark process` calls the authoritative part of the current runner: Echo Guard,
export/transcription, shadow timeline repair, local-recall audit, transcript-order audit,
group-overlap audit, the minimum audio-review evidence required by `audit_cleanup_v2`, extractive
synthesis and per-session readiness. It atomically writes
`derived/pipeline-run/authoritative_handoff.json`. The checkpoint contains the selected profile,
artifact paths, transcript SHA-256, verdict, ASR provenance, gate decisions, deferred state and the
exact next command. `status`, `next`, `transcript`, `notes` and `finish` consume it only while the
fingerprint and readiness profile still match.

`murmurmark enrich` runs the deferred part: rebuilt review clips, optional stronger local audio
judge, remote-forbidden/repair evidence, optional later cleanup profiles and live-vs-batch
comparison. Each deferred step has a bounded timeout. Deferred work may update its own report but
must not change the transcript fingerprint already published by the handoff. `audit_cleanup_v5`
remains a separate batch step after the suggested-review shadow report.

At the end of deferred enrichment the runner rebuilds the generic synthesis aliases, transcript-order
audit and session readiness using the profile recorded in the authoritative handoff. This lets late
evidence refine risks and next actions while keeping the published transcript path and SHA-256
unchanged. A missing or invalid handoff fails this final refresh instead of selecting a newer shadow
profile implicitly.
When the authoritative verdict still requires review and deferred work is pending, the handoff and
`murmurmark next SESSION` recommend `murmurmark enrich SESSION` before any manual review command.
Run `murmurmark next SESSION` again after enrichment to see only the unresolved remainder. Use
`--force-asr` when you need to regenerate Whisper output, and `--reuse-asr-cache` when you only want
to rebuild repair, cleanup, synthesis and reports from cached ASR JSON. The runner prints each stage
with `[run]`, `[passed]`, `[failed]` or `[skip]`, prints heartbeat lines for long-running stages, and
stores the final stage list in `derived/pipeline-run/pipeline_run_report.json`. While the run is
active it also writes `derived/pipeline-run/pipeline_run_state.json`, which lets
`murmurmark status SESSION` and `murmurmark next SESSION` show the active step, ASR chunk progress,
safe interrupt hint and resume command instead of trusting stale readiness. Heartbeats include the
step reason, checkpoint count when the step has known outputs, and a resume command, so a slow ASR
stage should not look like a silent hang. If you stop the runner with `Ctrl-C`, the current child
process is terminated, the run report is written with `status: interrupted`, `outcome` is refreshed,
and the printed next command is the same `murmurmark process SESSION` resume path. Use
`--progress-interval-sec 0` if you need a quieter run. Use `--plan-only` to print a compact
`pipeline_plan` with enabled/skipped stages, heavier stages, expected output files, `run_command`
for executing that plan and `current_next` for the current session state without executing the
pipeline; the plan JSON goes to `derived/pipeline-run/pipeline_plan_report.json`, not the last real
`pipeline_run_report.json`, and it does not refresh `outcome.json`. The CLI labels the following
readiness summary as `existing_readiness`. The run JSON stores plan metadata under `plan`, plus
top-level `recommended_next`, `next_commands` and `open_commands` for agent handoff; the
`pipeline_run` terminal block prints the same next commands from that report. After `process`
finishes, the last line is a single copyable `next: ...` command
from the current readiness state.

The default ASR runtime uses at most two track workers, six whisper.cpp compute threads per process
and four independent micro-ASR workers. Mic, remote and micro results are consumed in stable order,
and candidate scoring remains deterministic. The measured three-session corpus gives cold handoff
p50 `744.571s`, p95 `880.090s` and exact sequential-baseline transcript/profile/quality
equivalence. A forced run clears stale chunk progress before starting. If a child step times out or
is interrupted, the runner terminates the complete process group so no hidden ASR worker survives
the failed run.
The usual summary commands (`status`, `report`, `open`, `audit`, `cleanup`, `repair`, `synthesize`,
`notes`, `transcript`, `review`, `export`, `retention`) use the same convention. Pure output modes
such as `--path-only`, `--command-only` and `--cat` do not append a handoff line.
`murmurmark self-test` is the fast regression for this user-facing contract: it creates a tiny
processed fixture and then walks `process --plan-only`, review workspace/lane apply, `status`,
`report`, `next`/`open`, `export` and `retention` through CLI commands only. The direct
`scripts/smoke-cli-handoff.sh` entry point remains available when debugging the fixture itself.

For the usual record-then-process flow:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
murmurmark process "$SESSION" --full
murmurmark status "$SESSION"
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark finish "$SESSION"
```

For the main speaker-playback scenario, no headphones or acoustic-mode flag is required. Echo Guard
classifies measured remote-to-mic coupling automatically. Inspect the evidence with:

```bash
murmurmark inspect "$SESSION" --echo
jq '.acoustic_mode, .input_conditioning, .decision' \
  "$SESSION/derived/preprocess/echo/local_fir_report.json"
```

The completed `speaker_mode_hardening_v1` corpus remains a shadow experiment because its transcript
profile did not pass promotion gates. Reproduce the bounded report without changing selected
profiles or raw CAF with:

```bash
.venv/bin/python scripts/speaker-mode-hardening.py classify
.venv/bin/python scripts/speaker-mode-hardening.py audit-echo
.venv/bin/python scripts/speaker-mode-hardening.py profile || true

less sessions/_reports/speaker-mode-hardening-v1/speaker_mode_hardening_corpus_report.md
```

The expected decision is the recorded `DO_NOT_PROMOTE`, so `profile` deliberately exits non-zero.
The report is still complete and reproducible. Do not manually select `speaker_mode_hardening_v1`
for normal meetings until a new mixed-utterance hypothesis passes the frozen corpus.

After a successful recording, `record` prints `SESSION="..."`, `recommended_next` and the exact
`murmurmark process ...` command for that session. Use the same `$SESSION` for the whole handoff;
`latest` is only a recovery shortcut when no newer session can appear. If capture ended unexpectedly, `record` exits with an error after writing
a partial session. In that state, use `murmurmark inspect SESSION` first; `status`, `next` and
`process` do not treat the session as complete, and `process` requires explicit `--allow-partial`
for debugging.
If `finish` blocks, follow its printed review commands first, then rerun the same finish command.
For terminal handoff, prefer the last `next: ...` line when it is present; it repeats the primary
safe command after detailed status, review, export, finish or retention blocks.

Read `derived/readiness/session_readiness.md` before using a meeting result. It contains:

- `ready_for_notes`: the notes can be used with normal caution;
- `review_first`: check the listed audio-review regions before using the result for medium-risk work;
- `do_not_use_without_manual_review`: the transcript is too risky for unattended use;
- `pipeline_incomplete`: rerun the full pipeline before judging the session.

`ready_for_notes` is intentionally narrower than `exportable`. The readiness metrics include
`notes_review_burden_*` for selected evidence-backed notes and `transcript_review_burden_*` for the
full transcript/export surface. `notes_needs_review_*` is scoped to utterances used by selected
notes, while `needs_review_*` still describes the full transcript. If `export_blockers` is not
empty, the notes may still be usable, but `murmurmark export` blocks the bundle by default.

It also contains `Next Commands`: the shortest CLI path from the current state, such as rerunning
`murmurmark process`, building the first recommended review lane, exporting Markdown, or planning
retention. For `review_first`, the command chain runs `murmurmark review progress --session ...`
after workspace answers have been copied, then ends with `murmurmark review apply` only when the
decision file is ready. The JSON also includes `recommended_next`, which is the primary
action-oriented command for agents, and `open_commands`, which are read-only `less ...` commands for
the selected verdict, notes, transcript and audit reports.

`murmurmark status SESSION` prints a short status, `recommended_next`, a `use` block with read/export
booleans, blocker and minimum step, handoff commands for opening the selected notes/transcript/verdict,
the selected profile, verdict, review burden and synthesis review item summary without recomputing
reports. Use `murmurmark report SESSION` when readiness should be refreshed first. Both commands end
with the final copyable `next: ...` command. If readiness is missing, `status` points to
`murmurmark process SESSION`.
Use `murmurmark sessions` to list recent session packages with their current readiness state and
next safe command before choosing a target. The list includes label, creation time, duration and
review burden when readiness has those metrics. Use `murmurmark sessions --status review_required
--next-only` to print the current review queue as commands, `--status exportable --path-only`
when an export script needs only ready session paths, or `--status exported --next-only` when the
next action should be retention planning. Add `--json` when an agent needs structured
`session`, `label`, `duration_sec`, `review_burden_sec`, `status`, `gate`, `profile`, `verdict` and
`next` fields.
Use `murmurmark next SESSION` when you only need the single command to run now; add `--refresh` when
derived artifacts changed and readiness should be regenerated first. After a successful default
export, `status`, `sessions` and `next` follow the successful `export_manifest.json` and point to
retention planning. `next corpus` follows the same default manifest when its first corpus command
would otherwise repeat export for an already-exported session. If the export used a non-default
output directory, pass `--export-manifest ./path/to/export_manifest.json` to `next`.
Use `murmurmark next corpus` when operational-readiness reports already exist and you need the one
next command across the whole working-meeting corpus. Add `--refresh` when session-quality,
operational-readiness and the first recommended lane pack should be regenerated first, but heavier
corpus diagnostics should stay as-is.
If the focus session's recommended lane pack already exists, `next corpus` switches from “build the
lane pack” to the prepared review handoff: `afplay`, `less`, `$EDITOR`, then lane apply. If the
answer sheet already contains reviewed answers, the handoff promotes lane apply `--dry-run` ahead of
replaying the audio. In that prepared-pack mode, `review_actions` is still the corpus-wide remaining
queue, while `focus_pack_items`, `focus_pack_rows`, `focus_pack_minutes` and `after_focus_pack_*`
describe the next concrete review step and its expected effect.
When a lane is hard to classify from the transcript text alone, run the optional audio probe:

```bash
.venv/bin/python scripts/probe-review-lane-pack-audio.py \
  sessions/<session>/derived/readiness/review-plan/lane-packs/review_lane_pack.<lane>.json

less sessions/<session>/derived/readiness/review-plan/lane-packs/review_lane_probe.<lane>.md
```

The probe reruns whisper.cpp on each per-track clip in the lane pack (`mic_clean`,
`mic_role_masked`, `remote`) and writes a Markdown/JSON comparison. Treat it as review evidence,
not as an automatic decision source.
Use `murmurmark open SESSION` when you need the selected local artifact rather than the next action.
It resolves paths from `session_readiness.json` and prints `less ...` commands for notes,
transcript, verdict, readiness and audit reports; `--cat` streams one artifact to stdout.
The rule of thumb is simple: `status` reads the current dashboard, `report` refreshes it after
review, cleanup, export, retention or other derived artifacts changed.
The terminal output is enough to see whether notes still depend on risky
utterances before opening the Markdown files. `recommended_next` prefers actionable `murmurmark ...`
commands from `next_commands`; report-reading commands remain visible in `next` and `open`.
Use `murmurmark review next SESSION` when you only need the next review step for one session. It
refreshes session readiness, builds a session-local review plan under
`SESSION/derived/readiness/review-plan/` when review is needed, and prints the review-oriented
command chain. Pass `--no-refresh` to read an already current `session_readiness.json`, or
`--no-plan` to refresh readiness without rebuilding the session-local plan.

When you want one final handoff, use `finish`:

```bash
murmurmark finish "$SESSION"
```

It refreshes readiness, attempts the guarded export with JSON evidence, writes retention plan and
provider payload manifest after a successful export, and ends with a read-only `less ...` command for
the bundle. Open `index.md` first. It is the user-facing start page: verdict, selected profile,
review burden, links to notes/transcript/evidence, retention/privacy summary and the next command.
`quality_verdict.md` explains trust, `notes.md` contains evidence-backed extractive notes, and
`transcript.md` keeps the full selected transcript with utterance IDs and review flags.

If export is still blocked, `finish` writes the blocked export report and points back to the next
review or processing command. A blocked session should not be treated as a successful final handoff.

For low-level inspection, the individual commands remain available:

```bash
murmurmark export "$SESSION" --format markdown --include-json
murmurmark retention plan "$SESSION"
murmurmark retention payload "$SESSION"
less "$SESSION/derived/retention/retention_plan.json"
less "$SESSION/derived/retention/provider_payload_manifest.json"
```

The CLI prints a compact summary after each retention command, including action counts,
blockers/warnings, `open` commands for the local manifests and the next safe command as a final copyable `next: ...` line. If readiness is
not exportable yet, retention points back to `murmurmark process` or `murmurmark review next`
instead of suggesting a blocked export.
The JSON artifacts store the same handoff: `retention_plan.json` and
`provider_payload_manifest.json` include `recommended_next`, `next_commands` and `open_commands`.
The JSON files remain the source of truth.

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

murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
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

When called directly, it resolves the MurmurMark executable as `MURMURMARK_BIN`, then `murmurmark`
from `PATH`, then `.build/debug/murmurmark` as a development fallback.

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

Raw Whisper results are cached in `raw/mic.json` and `raw/remote.json`. In default `windowed` mode,
each ASR window also has its own cache under `raw/chunks/<track>/`. The cache is reused only when
the model, language, prompt, duration limit, `--max-context`, ASR window settings, audio preparation
settings, source-audio fingerprint and window boundaries match the current run.
Use `--force` to regenerate raw Whisper output anyway. Changing `--prompt-file` also changes the
raw cache metadata, so the next non-skipped run will regenerate raw ASR output.

When the top-level `raw/mic.json` or `raw/remote.json` is missing but valid chunk JSON files remain,
the bridge reuses those chunks and rebuilds the combined raw JSON instead of rerunning `whisper-cli`
for every window. Inspect chunk reuse here:

```bash
jq '{track,status,chunks_completed,chunks_total,chunks_reused,chunks_transcribed,completed_hard_sec,total_sec}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json"

jq '{track,status,chunks_completed,chunks_total,chunks_reused,chunks_transcribed,completed_hard_sec,total_sec}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/raw/chunks/remote/chunk_cache_report.json"
```

The normal `murmurmark process` runner also executes a light rebuild check after `transcribe_current`:

```bash
scripts/check-asr-chunk-cache.py "$SESSION" --require-chunks
jq '.status, [.tracks[] | {track,status,raw_rows,rebuilt_rows,chunks_completed,chunks_total}]' \
  "$SESSION/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json"
```

This check fails the pipeline if the current `raw/mic.json` or `raw/remote.json` cannot be rebuilt
from cached chunks. It is the first no-regression gate for chunked ASR reuse.

For a corpus-level view:

```bash
scripts/report-asr-chunk-cache-corpus.py --refresh --no-fail
less sessions/_reports/asr-chunk-cache/asr_chunk_cache_corpus_report.md
scripts/check-corpus-gates.py --no-fail
```

`check-corpus-gates.py` treats a failed ASR chunk-cache corpus report as a hard gate failure. Missing
coverage is still a warning while Chunked/Resumable Processing v1 is being rolled out.
It also reads `sessions/_reports/live-pipeline/live_corpus_gates_report.json`: live/near-realtime
cache promotion must remain blocked. The live comparison records measurable capture-safety, order,
local-recall, remote-leak, review-burden, notes-readiness and chunk-boundary gates. Boundary
suppression is split into resolved duplicates and unresolved suppressions, so a fully covered
overlap repeat can pass while unique-word loss still blocks promotion. Current diagnostic live
coverage is separated from date-named real meeting evidence. Only real meeting sessions contribute
to `real_parity_dimensions` and strict promotion gates; `_debug_*` and `live-pilot-*` sessions remain
useful failure evidence but cannot make live promotion look ready. Strict parity is still not proven;
the batch transcript remains authoritative and live promotion remains disabled in v1. Text mismatch
between live draft and batch transcript is tracked as `draft_text_recall`, separate from missing
comparison artifacts.
Once the full capture fail-open proof exists, the live corpus report also writes
`capture_safe_candidate_scope` and `real_capture_safe_candidate_parity_dimensions`. This is the
candidate-only view for real sessions that already passed capture safety and required artifact gates.
Use it to see which parity dimensions still block a safe live branch after old broken-capture runs
are excluded. It is not a promotion flag: live output remains shadow-only and batch remains
authoritative. Its `next_focus` names the next blocker inside that capture-safe candidate slice.

For the current boundary-island blocker, run the diagnostic micro-ASR lab:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py --max-candidates 5
less sessions/_reports/live-pipeline/live_boundary_island_micro_asr_lab.md
```

The lab re-decodes selected local islands from live chunk audio and batch-reference mic sources.
It is evidence-only: current output finds `1` live alignment candidate / `5.10s`, keeps
`publication_ready_seconds = 0.0`, and does not modify live drafts, batch transcripts or promotion
gates. After the lab report exists, `scripts/compare-live-batch.py` materializes the diagnostic
profile
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_live_boundary_micro_asr_lab_shadow_v1`.
Use the corpus report to inspect it:

```bash
jq '.live_target_me_shadow_profile_diagnostics.real.best_profile' \
  sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

This profile is `lab_shadow`, not live-implementable. It is useful only to measure the ceiling of
the micro-ASR idea under normal parity gates.

To test the live-implementable side of the same idea, run the live-only candidate mode:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-only \
  --max-candidates 10 \
  --source-scope live

scripts/report-live-corpus-gates.py --sessions-root sessions --refresh

jq '.live_target_me_shadow_profile_diagnostics.real.profiles[]
    | select(.policy | contains("live_boundary_micro_asr_live_only_shadow_v1"))' \
  sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

This writes `live_boundary_micro_asr_live_candidates_lab.*`. Current corpus result: `3`
alignment candidates / `13.76s` are found, but the materialized live-only profile adds `0.00s`
after deduplication and safety gates. Treat that as a useful negative result: micro-ASR is wired
into the live-implementable path, but candidate selection still needs better speaker/boundary
evidence before it can close the local-recall gap.

To inspect the duplicate-heavy blocker named by the live corpus report, run:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-duplicate-heavy \
  --source-scope live

less sessions/_reports/live-pipeline/live_duplicate_heavy_micro_asr_live_candidates_lab.md

scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source blocker-analysis \
  --source-scope live

less sessions/_reports/live-pipeline/live_duplicate_heavy_micro_asr_lab.md
```

`live-duplicate-heavy` uses only live-available suppressed-mic segment features and writes
`live_duplicate_heavy_micro_asr_live_candidates_lab.*`. `blocker-analysis` reads
`capture_safe_candidate_local_recall_blocker_analysis`, writes `live_duplicate_heavy_micro_asr_lab.*`,
and stays batch-informed. Use both to compare the live-only selector with the batch-informed ceiling.
Neither report changes live drafts or promotion gates.

Older sessions may have top-level `raw/mic.json` and `raw/remote.json` from pre-chunk runs but no
`raw/chunks/<track>/chunk_cache_report.json`. In `windowed` mode that legacy raw cache is no longer
enough for `murmurmark process`: the transcript step rebuilds per-window chunks first, then
`check-asr-chunk-cache.py --require-chunks` proves that the combined raw ASR can be reconstructed.

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

jq '{recommended_next, next_commands: [.next_commands[].id], open_commands: [.open_commands[].id]}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark notes "$SESSION" --path-only
murmurmark transcript "$SESSION"
murmurmark open "$SESSION" --kind notes
murmurmark open "$SESSION" --kind transcript --command-only
less "$(murmurmark transcript "$SESSION" --path-only)"
```

`quality_verdict.json` and `synthesis_manifest.json` both include `recommended_next`,
`next_commands` and `open_commands`. Use those fields for automation; the terminal output is only the
human-readable view of the same handoff.

### Empty but valid sessions

If nobody speaks, a correct transcript is empty. Synthesis accepts that result only when it can
prove `session_classification: verified_no_speech` from complete raw capture, matched track
durations, microphone acoustic activity, silent remote audio, successful ASR chunk reconstruction,
known-hallucination-only ASR output and a clear local-recall audit:

```bash
jq '{verdict, session_classification, failures: .metrics.no_speech_evidence.failures}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

jq '.' \
  "$SESSION/derived/synthesis-simple/extractive/no_speech_evidence.json"
```

Expected verified result:

```text
verdict: good
session_classification: verified_no_speech
failures: []
```

Do not treat an arbitrary empty transcript as a no-show. A silent microphone, incomplete capture,
unexpected capture warning, missing chunk proof, possible lost `Me` evidence or unfiltered ASR text
keeps the verdict at `failed`.

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
review seconds, `read: less ...` for the Markdown report and `recommended_next`. If possible lost
local speech is present, `recommended_next` points back into `murmurmark review next SESSION`.

The baseline part of the audit reads timeline-repair examples and Echo Guard `speaker_state.jsonl`.
When `derived/live/causal-target-me/candidates.jsonl` exists, it also performs an independent check:
causal Target-Me candidates are compared with the best available batch dialogue. Candidates that
have local-speaker evidence, pass remote-text/localization guards and are absent from both batch
`Me` and authoritative remote text become `possible_lost_me` rows with
`evidence_source: live_causal_target_me`. They remain review-only and never edit the transcript.
This closes the circular blind spot where timeline repair could report no missing island because it
had never detected the local phrase in the first place.

The audit writes under `derived/audit/local-recall/`, does not edit transcripts, and is used by
`report-session-quality.py` to decide whether low local recall should block `ready_for_notes`.
`summary.audited_missing_island_count` still counts only timeline-repair islands;
`independent_live_me_evidence_count/seconds` report the additional evidence separately. The
pipeline pins both timeline and dialogue to `shadow_v2` for reproducibility. A later manual audit
may omit `--dialogue-profile` to compare against the best available reviewed profile.
An existing but empty `timeline_repair_examples*.jsonl` means timeline repair found no unrecovered
local islands; the audit should finish as `status: ok` with zero items.
If an unrecovered local island is already covered by nearby remote transcript text, the audit labels
it `likely_harmless_remote_covered`: content is preserved, and the remaining risk is attribution,
not missing meeting substance. Short islands on parent, recovered-child or remote-guard boundaries
are labelled `likely_harmless_boundary_fragment`; they stay visible in audit JSON but should not
inflate the review queue unless other local-speech evidence is strong. Short acknowledgement-only
islands such as `понял` or `окей` are labelled `likely_harmless_ack_fragment`; they remain visible
for audit, but do not become blocking lost-speech evidence by themselves.
If a short boundary island contains work markers but nearby remote text already covers enough of its
meaningful tokens, it is labelled `likely_harmless_remote_boundary_covered`; this keeps attribution
risk visible without turning a preserved remote phrase into mandatory lost-local review.

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
local tail after it; this is included in session readiness review burden and should go to review
before using the transcript for precise chronology. When such rows exist, the CLI `recommended_next`
points to `murmurmark review next SESSION`.

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
jq '{recommended_next, next_commands: [.next_commands[].id], open_commands: [.open_commands[].id]}' \
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
The repair report owns the next-step handoff: `murmurmark repair order` prints the same
`recommended_next` and `next_commands` that are stored in JSON.

For corpus work, aggregate order risks after the usual session-quality report:

```bash
murmurmark corpus order
murmurmark corpus order --repair
murmurmark corpus local-recall
murmurmark corpus local-recall --audit
murmurmark corpus remote-leak
murmurmark corpus remote-leak --plan

jq '.summary' sessions/_reports/transcript-order/transcript_order_corpus_report.json
less sessions/_reports/transcript-order/transcript_order_corpus_report.md
jq '.summary' sessions/_reports/local-recall/local_recall_corpus_report.json
less sessions/_reports/local-recall/local_recall_corpus_report.md
jq '.summary' sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.json
less sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.md
```

Without `--repair`, the command only aggregates existing order audits. With `--repair`, it uses the
sessions from the current session-quality report, refreshes their order audit, writes conservative
`order_repair_v1`, refreshes session-quality and then rebuilds the corpus order report. Pass explicit
session paths or `all` when you need a different target set. This report is the practical list of
chronology regression candidates. Its `summary.order_repair` block shows the corpus-level repair
effect: sessions with repair, cleared sessions, applied repairs, remaining unrepaired order risks and
resolved order-risk seconds.
For local recall, `--audit` refreshes per-session `audit-local-recall.py`, refreshes session-quality
and then rebuilds the corpus queue for possible lost `Me` islands.
For remote leak, `--plan` refreshes only the audit-only segment plans, then rebuilds the corpus
summary. It still does not apply transcript edits.
Selected operational sessions with blocking order risk fail `check-corpus-gates.py` through
`transcript.no_blocking_order_risk`; partial or diagnostic historical sessions remain visible as
review material without blocking the operational gate. `check-corpus-gates.py` also reads the
aggregate report itself and fails `transcript_order.no_complete_blocking_sessions` only when a
selected operational session remains blocked by chronology risk.

### Authoritative boundary closure

Use this corpus step after order, local-recall and audio-review evidence has been built. It freezes
the currently selected operational profiles; do not use `--force` merely to pick up later generated
candidates.

```bash
murmurmark report corpus
murmurmark corpus boundary freeze
murmurmark corpus boundary run
murmurmark report corpus

jq '{decision, summary, gates}' \
  sessions/_reports/authoritative-boundary-v1/boundary_corpus_report.json
less sessions/_reports/authoritative-boundary-v1/boundary_corpus_report.md
```

For a single session already present in the frozen scope:

```bash
murmurmark repair boundary "$SESSION"
murmurmark synthesize "$SESSION" --transcript-profile authoritative_boundary_v1
murmurmark report "$SESSION"
```

The command does not rerun ASR. It can keep an evidence-confirmed overlap, drop a two-judge whole
duplicate/noise `Me` utterance or split a long `Me` turn at source-segment boundaries. Every other
row remains `needs_review`. `auto`, `status`, notes and export use the profile only after the global
decision is `PROMOTE_AUTHORITATIVE_BOUNDARY_V1`, the session is in `promoted_sessions`, the frozen
input SHA values still match and the promoted output fingerprint is current. Reprocessing a source
profile makes the boundary result stale by design; rerun the corpus boundary command before relying
on it again.

Useful per-session artifacts:

```bash
jq '{input_profile, summary, gates}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/authoritative-boundary-v1/boundary_repair_report.json"
less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.authoritative_boundary_v1.md"
```

If evidence is missing or changed, the row fails open; repair never falls back to a text guess. A
failed corpus decision leaves all previous profile selection unchanged.

### Residual Me evidence closure

After boundary promotion, the residual corpus pass adds exact local audio, Target-Me,
remote-forbidden and local micro-ASR evidence without rerunning primary ASR:

```bash
.venv/bin/python scripts/residual-me-evidence.py freeze
.venv/bin/python scripts/residual-me-evidence.py evidence
.venv/bin/python scripts/residual-me-evidence.py evaluate --apply --synthesize

jq '{decision, summary, gates}' \
  sessions/_reports/residual-me-evidence-v1/residual_me_corpus_report.json
less sessions/_reports/residual-me-evidence-v1/residual_me_corpus_report.md
```

Use `freeze --force` only when intentionally replacing the immutable baseline. Normal reruns verify
the existing queue. `evidence` uses the local faster-whisper `large-v3` model and Target-Me
enrollment; absent models or unusable clips remain `needs_review` instead of failing the batch
profile.

The promoted run closes `31/124` rows / `170.589/478.272s`. It can keep a proven local utterance,
drop a proven whole remote duplicate/noise row, insert only word-timestamp-backed missing local
fragments, or record existing `Me` coverage/overlap. Existing remote rows and existing utterance text
remain exact. The per-session audit is available at:

```bash
jq '{input_profile, summary, gates}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/residual-me-evidence-v1/residual_me_evidence_profile_report.json"
less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.residual_me_evidence_v1.md"
```

`auto`, status, readiness, notes and export prefer `residual_me_evidence_v1` only after the global
`PROMOTE_RESIDUAL_ME_EVIDENCE_V1` decision, per-session gates, promoted-session membership, frozen
source hashes and output fingerprints all match. Otherwise selection falls back to
`authoritative_boundary_v1`. This command is a frozen-corpus maintenance path; process new sessions
with the normal `murmurmark process` command.

### Residual audio evidence arbitration

The largest remaining audio-review class can be reproduced without changing the promoted profile:

```bash
.venv/bin/python scripts/residual-audio-arbitration.py freeze
.venv/bin/python scripts/residual-audio-arbitration.py evidence
.venv/bin/python scripts/residual-audio-arbitration.py evaluate --apply --synthesize

jq '{decision, summary, gates, evidence_limit}' \
  sessions/_reports/residual-audio-arbitration-v1/residual_audio_corpus_report.json
less sessions/_reports/residual-audio-arbitration-v1/residual_audio_corpus_report.md
```

The evidence pass isolates sessions in child processes so the local speaker encoder does not retain
corpus-wide memory. It reuses SHA-bound local faster-whisper word-timestamp and stronger-judge
results, then adds exact/whole/speaker-bounded clips and per-session remote-negative calibration.

Current result: `DO_NOT_PROMOTE`. Every one of the `66` rows / `196.920s` has a stable outcome, but
only `1` row / `0.640s` closes safely. The required floor is `14` rows / `39.384s`. Do not use the
candidate as the final transcript; `auto` intentionally keeps `residual_me_evidence_v1`.

### Residual local recall closure

The separate local-recall queue is reproducible without rerunning primary ASR:

```bash
.venv/bin/python scripts/residual-local-recall.py freeze
.venv/bin/python scripts/residual-local-recall.py evidence
.venv/bin/python scripts/residual-local-recall.py evaluate --apply --synthesize

jq '{decision, summary, gates, synthesis_gates}' \
  sessions/_reports/residual-local-recall-v1/residual_local_recall_corpus_report.json
less sessions/_reports/residual-local-recall-v1/residual_local_recall_corpus_report.md
```

Use `freeze --force` only to intentionally replace the immutable baseline. The promoted result
classifies all `13` rows / `48.073s` and closes `9` rows / `26.953s` without inserting speech. Four
rows remain explicit because local evidence is weak or mixed. `auto`, status, readiness, notes and
export select `residual_local_recall_v1` only while corpus, session, source-hash, fingerprint,
verdict and note-evidence gates all pass.

## Remote Leak Segment Plan

Remote leak and partial remote duplicates need a different safety shape. A `remote_leak` region or
a `remote_duplicate` row can still contain real local content from `Me`, so the next safe step is an
audit-only segment plan:

```bash
murmurmark repair remote-leak "$SESSION"

less "$SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md"
jq '.summary, .action_plan, .policy' \
  "$SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
jq '{recommended_next, next_commands: [.next_commands[].id], open_commands: [.open_commands[].id]}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
```

The planner reads `derived/audit/audio-review-pack/audio_review_audit.jsonl`, selects
`remote_leak` rows and partial `remote_duplicate` rows with `probable_transcript_error`, and writes only
`remote_leak_segment_repair_*` artifacts. It does not edit transcript profiles, Echo Guard outputs,
or raw CAF files. Items with unique local `Me` content are labelled
`remote_leak_with_local_content_risk` or `remote_duplicate_with_local_content_risk`; those are future
segment-level repair candidates, not whole-utterance drops. The plan stores the same
`recommended_next`, `next_commands` and `open_commands` that `murmurmark repair remote-leak` prints
after writing it. The full `murmurmark process` pipeline
runs this planner automatically after audio-review; the manual command is useful when you have
refreshed only the audio-review audit.

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

jq '{recommended_next, next_commands: [.next_commands[].id], open_commands: [.open_commands[].id]}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json"
```

This stage writes a new transcript profile and leaves `current` and `shadow_v2` untouched. It only
drops whole `Me` utterances when the group-overlap audit and local safety checks agree. It keeps
double-talk, timing overlap, remote leak, and human-review cases, but adds audit flags to affected
utterances. The cleanup report includes `recommended_next`, `next_commands` and `open_commands`; the
`murmurmark cleanup` terminal summary prints the same handoff.

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
audio-judge demand, `read: less ...` for the Markdown report and `recommended_next`. Probable or
uncertain transcript errors point back into `murmurmark review next SESSION`.

If the local `faster-whisper` judge is installed, run it over the same pack before building review
lane packs when you deliberately want a heavier audio check. For a concrete blocker, prefer the
targeted lane-pack mode after `review first-lane`. It resolves current audio-review items by stable
utterance ids instead of fragile `arp_*` numbers:

```bash
murmurmark review first-lane --session "$SESSION"

LANE=check_unique_me_content
murmurmark audit stronger-audio-judge "$SESSION" \
  --review-lane-pack "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_pack.$LANE.json" \
  --quick \
  --max-items 5 \
  --max-computed-items 5

jq '{items, suggested_keep_me_seconds, suggested_drop_me_seconds, skipped_reason}' \
  "$SESSION/derived/audit/audio-review-pack/faster_whisper_judge_summary.json"

less "$SESSION/derived/audit/audio-review-pack/faster_whisper_judge_report.md"

murmurmark review first-lane --session "$SESSION"
murmurmark review lane apply "$LANE" --session "$SESSION" --answers-source suggested --dry-run
```

The stronger judge is local and optional. It decodes only short review clips, writes
`faster_whisper_judge.jsonl`, `faster_whisper_judge_summary.json` and
`faster_whisper_judge_report.md`, and never changes transcript profiles by itself. It prints progress
while loading the model and decoding items. For ad-hoc CPU debugging, use targeted lane-pack batches
with `--quick --max-items 5 --max-computed-items 5`; for the normal full session process, keep the
default broader budget so suggested review can close order/timing rows before asking for manual
listening. Review lane packs use those rows only for safer suggested answers: `confirm_me` and
`confirm_timing_or_doubletalk` suggest `keep_me` when allowed; `confirm_remote_duplicate` and
`confirm_asr_noise` suggest `drop_me` only when the lane and safety gates allow that decision.
Suggested answer sheets leave
`uncertain`/`needs_review` rows as dots, so applying suggested answers closes only actionable rows.
Dots preserve already reviewed rows and do not reset earlier decisions back to `todo`.

The pack is written under `derived/audit/audio-review-pack/`. It includes short `mic_raw`,
`remote`, `mic_clean`, `mic_role_masked` and stereo comparison clips for suspicious transcript
regions. The local audit classifies each item as likely reliable, probable transcript error, or
needing a stronger local audio judge. It does not rewrite transcripts, Echo Guard outputs,
synthesis files or raw `audio/*.caf`.
If there are no suspicious regions, the pack may contain zero items. That is a normal no-op: the
audit writes empty `audio_review_audit.jsonl`, `audio_review_summary.json` and
`audio_review_report.md`, then the full `murmurmark process` pipeline continues.
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
review burden for `ready_for_notes` follows only selected evidence utterance IDs. Transcript/export
review burden remains in `transcript_review_burden_*` and `export_blockers`.
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
murmurmark corpus build \
  sessions/<session-1> \
  sessions/<session-2> \
  --per-label 16 \
  --max-items 160

murmurmark corpus evaluate

murmurmark corpus train-audio-judge

murmurmark corpus taxonomy

murmurmark corpus gate
murmurmark corpus gate \
  --write-baseline sessions/_reports/corpus-gates/baseline.local.json
murmurmark corpus gate \
  --baseline sessions/_reports/corpus-gates/baseline.local.json

murmurmark review next "$SESSION"
murmurmark review first-lane
murmurmark review "$SESSION" --lane fast_confirm_drop
murmurmark review progress --session "$SESSION"
murmurmark review apply --session "$SESSION"
murmurmark export sessions/<session> --format markdown --include-json

less sessions/_reports/regression-corpus/regression_corpus.md
less sessions/_reports/regression-corpus/regression_corpus_evaluation.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_report.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_cv_predictions.jsonl
less sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.md
less sessions/_reports/operational-readiness/operational_readiness_report.md
less sessions/_reports/review-plan/review_plan.md
```

The first three corpus commands form a CLI ladder: `corpus build` prints
`recommended_next: murmurmark corpus evaluate --corpus-dir ...`, `corpus evaluate` prints
`recommended_next: murmurmark corpus train-audio-judge --corpus-dir ...`, and
`corpus train-audio-judge` prints `recommended_next: murmurmark corpus taxonomy ...`. Each stage
also prints `read: less ...` for its local report and repeats the primary action as the final
copyable `next: ...` line. The same continuation is stored in each stage JSON as
`recommended_next`, `next_commands` and `open_commands`.

For a full refresh with all sessions under `./sessions`, use:

```bash
murmurmark corpus process all --per-label 16 --max-items 160
```

`corpus gate` reads the generated session-quality, regression-corpus, audio-judge, transcript-order,
local-recall, remote-leak segment corpus and operational readiness JSON reports, then writes
`sessions/_reports/corpus-gates/corpus_gates_report.json` and `.md`. Hard readiness checks are
scoped to selected operational sessions. Local-recall floor and per-session review-burden ratio are
hard only for `ready_for_notes` sessions; `review_first` sessions remain visible through operational
review queue checks and warnings. The operational queue is checked both as raw rows and as packed
human actions: default limits are `25` rows and `15` actions, with the current corpus at `12` rows
and `9` actions. Full historical corpus blockers from diagnostic sessions, stale
raw audit profiles or non-blocking short review items are warnings. Remote-leak segment queues are also warnings: they are
review/repair backlog, not hard no-regression failures. `passed` or `passed_with_warnings` exits
successfully. `failed` exits non-zero unless `--no-fail` is used, but the CLI still prints
`read: less ...` and a final copyable `next: less ...` line first. The JSON stores the same handoff
in `recommended_next`, `next_commands` and `open_commands`.

For risky algorithm changes, first save a private local baseline:

```bash
murmurmark corpus gate \
  --write-baseline sessions/_reports/corpus-gates/baseline.local.json
```

Then compare future corpus refreshes with it:

```bash
murmurmark corpus gate \
  --baseline sessions/_reports/corpus-gates/baseline.local.json
```

The baseline check catches drops in complete or `ready_for_notes` sessions, growth in `review_first`,
review burden increases, audio judge training/accuracy regressions, lost baseline sessions and
per-session drops in local recall or use gate. It also catches growth in complete-session
local-recall blockers, possible lost-`Me` seconds and protected remote-leak queue items/seconds.
The baseline file lives under ignored generated reports and must not be committed when it is built
from real meetings.

`murmurmark export` is the user-facing handoff. It reads the selected transcript profile from
per-session readiness, copies the Markdown verdict, notes and transcript into `exports/private/`,
and writes `export_manifest.json`. It blocks sessions with readiness `export_blockers` by default;
blocked export prints structured next commands from readiness plus rerun/debug export commands. Use
`murmurmark review next SESSION` first when review is required, or pass `--force` only for debugging.
Forced exports with blockers keep retention commands under `debug_retention` and keep the primary
handoff on the unfinished `process` or `review` work.
After a successful export, the CLI prints the manifest path, key output files and the retention
commands that should use the same manifest, then repeats the primary handoff as a final copyable
`next: ...` line. The manifest itself is also a continuation artifact:
`next_commands` stores retention commands, `open_commands` stores read-only inspection commands, and
`export_commands` stores the safe rerun/debug-force commands. Forced exports keep readiness repair
or review in `next_commands` and put retention under `debug_retention_commands`.

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
model/confidence gaps. It also writes diagnostic subtypes such as `uncertain_duplicate_vs_leak`,
`remote_leak_with_local_content_risk` and `remote_duplicate_with_local_content_risk`; use those
subtypes to choose the next narrow repair instead of retuning all audio-review labels at once. Its
`action_plan` section is the shortest handoff for the next agent: it names the diagnostic subtype,
the next work item and the expected deliverable. The CLI output is read-first: it prints
`read: less sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.md`, follow-up
commands and a final copyable `next: less ...` line.
The operational readiness report answers whether the current pipeline is usable for medium-risk
working meetings, how much manual review remains, which sessions are `ready_for_notes` versus
`review_first`, and which audio-review clips should be checked first. Its review queue is also
profile-aware: already-resolved cleanup items are not shown as remaining work. Its `next_commands`
points first to `murmurmark corpus process all` when structural pipeline coverage is the blocker,
otherwise to the first review lane/workspace commands. When the report has a concrete focus session,
those review commands include `--session sessions/<id>` and do not ask the operator to infer the
target from the queue.
`murmurmark report corpus` prints the same operational handoff in compact form: the first
`next_command`, plus `focus_session`, `focus_label`, `focus_reason` and `focus_next` when a concrete
review target exists. It also prints `sessions_in_scope` and `sessions_excluded` to make the
working-meeting scope visible next to the full corpus count. Smoke/diagnostic recordings and
interrupted partial captures are excluded from this operational scope but remain visible in the full
session-quality report. The same block prints
`review_actions` and `grouped_review_rows`, so the handoff shows the number of actual answer-sheet
decisions rather than only the noisier raw row count. It also prints `low_materiality_review_rows`
when very short low-content `remote_leak` / `uncertain` tails or short low-content transcript-order
overlaps were kept in the report but excluded from the mandatory review queue. Those rows are not
treated as resolved; the field only prevents tiny non-material tails from taking the next review
slot.
The command also refreshes `sessions/_reports/review-plan/` from the just-written
operational-readiness report. That keeps the global review plan, `review first-lane`, and
`next corpus` aligned. The first lane is chosen by the largest blocking review lane in packed
actions, not by a fixed severity order; short local-recall tails remain explicit but no longer hide
the larger `check_unique_me_content` queue.
It also prints a `use` block with the practical corpus verdict: whether any notes are already
usable, whether the corpus is ready for medium-risk meetings, ready/review/incomplete session
counts, notes review burden and the minimum next command. Full transcript/export risk is separate:
`session_readiness.json` may show `use_gate: ready_for_notes` and still keep `export_blockers` such
as `full_transcript_review_required`. In that state notes can be used with normal caution, while a
default export remains blocked until transcript-only review is closed or `--force` is used
deliberately.
The 2026-07-02 corpus snapshot is the current convergence baseline: `murmurmark next corpus
--refresh` reports `pilot_ready_with_review`, `24` working sessions in scope and `26` diagnostic
sessions excluded. `15/24` sessions are `ready_for_notes`, nine are `review_first`, and no session is
`do_not_use_without_manual_review`. Selected notes carry about `1.32 min` of documented residual
review burden; the remaining full transcript/export surface is about `4.09 min`. The stronger local
audio judge now uses an `80` item budget in the normal process path, because the earlier `12` item
cap left avoidable order-risk rows in the manual queue on long meetings.

The stronger local audio judge can also close a narrow benign timing-overlap case: if group-overlap
evidence already shows strong local support and weak remote/leak support, it may suggest `keep_me`.
This is intentionally keep-only; conflicting double-talk and remote-active overlaps remain in the
manual queue.
Single-word `так` tails without action/decision/risk markers are treated as low-materiality instead
of mandatory review; this keeps discourse markers visible in the transcript without forcing audio
review for every harmless filler tail.
Short exact partial duplicates with no unique `Me` content are also treated as low-materiality
rather than mandatory review.

Session-quality reports de-duplicate transcript-only `uncertain` rows when the same selected `Me`
interval is already covered by high-confidence `likely_reliable` audio-review evidence. The
agent-review layer can also keep narrow local speech rows when speaker-state and audio-review
evidence support `Me`, and cleanup profiles can remove only tightly gated duplicate/noise material.
Possible lost `Me` speech, probable transcript errors and uncertain semantic content must stay
visible to review/export gates.

The same snapshot exposes a bounded irreducible mandatory review queue: `review_actions` is `9`
with `12` queue rows, below the corpus-gate limits of `15` actions and `25` rows. Another `28`
rows / `70.95s` are tracked as low-materiality and are
kept outside the mandatory queue. Suggested closure has no safe keep/drop rows left to apply; the
remaining local-recall, lost-`Me`, order and uncertain-audio rows are intentionally not auto-closed
by the current local agents. This is why the operational verdict is `pilot_ready_with_review` rather
than `medium_risk_ready`: selected notes can be used session-by-session when `status` says they are
ready, but the queue remains explicit until a human or a stronger local evidence layer closes it.
The operational JSON and Markdown report include `manual_tail_explanation`: a compact table of
remaining reasons, counts and seconds. Use it to distinguish true manual review from low-materiality
rows and from bugs where evidence exists but the review loop failed to consume it.
A narrow risky session is represented as `formal_residual_risk` when its remaining queue is short,
bounded and made only of allowed risk flags; it remains `review_first` and default export stays
blocked.
`murmurmark next corpus` is the compact action-only view of that same report. Without `--refresh` it
only reads `sessions/_reports/operational-readiness/operational_readiness_report.json`; with
`--refresh` it first rebuilds session-quality and operational-readiness reports, then prints
`corpus_next.command`, the same `use` summary, focus metadata, alternatives and a final copyable
`next: ...` line. When the focus lane pack already exists under
`SESSION/derived/readiness/review-plan/lane-packs/`, `corpus_next.source` becomes
`review_lane_pack` and the command becomes the actual next review action, usually `afplay` for the
assembled lane audio. The output also shows the prepared pack size and expected remaining queue:
`focus_pack_items` is the number of answer-sheet decisions to make now, `focus_pack_rows` is the
number of raw review rows covered by that pack, and `after_focus_pack_actions` is the corpus-wide
packed action count after this focused pack is applied.
When no actionable pack exists, `next corpus` keeps `command: murmurmark corpus report` and prints
`focus_reason: no_actionable_review_rows` plus `murmurmark status/report SESSION` as the honest
follow-up.
The generated lane pack Markdown includes the same `probe-review-lane-pack-audio.py` command for
digital per-track ASR evidence. This is useful for AI-agent review loops: it can show whether
`mic_clean`, `mic_role_masked` and `remote` all decode to the same phrase, which is exactly the case
where automatic cleanup should usually stay conservative.
This prepared-pack shortcut is freshness-gated: if the lane pack manifest is older than the current
operational-readiness report, `next corpus` ignores it and points back to
`murmurmark review first-lane --session ...`, so the reviewer does not listen to rows that the latest
agent-reviewed or cleanup pass has already closed.
Stale audio-judge queue rows are also ignored when the current audio-review audit has reclassified
the source item as reliable.
`murmurmark corpus order` writes a separate chronology-risk corpus report under
`sessions/_reports/transcript-order/`; read it when wrong reply order is the concern rather than
audio leakage. The report writes `next_commands`: complete blocking sessions go to
`murmurmark review lane check_transcript_order --session ...`, and incomplete sessions go back
through `murmurmark process ...`. The CLI keeps the symbolic `recommended_next_step` as
`recommendation`, then prints `read: less ...`, executable `recommended_next` and the final
copyable `next: ...` line.
`murmurmark corpus local-recall` writes `sessions/_reports/local-recall/`. It aggregates
possible lost `Me`, local-recall review and harmless missing-island explanations across the corpus.
Use it when local speech recall is the concern. The report writes `next_commands`: complete sessions
go to `murmurmark review lane check_local_recall --session ...`; incomplete sessions go back through
`murmurmark process ...`. The CLI uses the same read/recommended/final-`next` handoff as the order
corpus report. For a strong missed local island, use the candidate repair profile explicitly. The
repair script covers `possible_lost_me` rows and the narrow `needs_review` rows where speaker-state
says the island is almost pure `local_only` with no meaningful remote activity:

```bash
murmurmark repair local-recall "$SESSION" \
  --input-profile auto \
  --output-profile local_recall_repair_v1

murmurmark transcript "$SESSION" --profile local_recall_repair_v1
murmurmark synthesize "$SESSION" --transcript-profile local_recall_repair_v1
jq '{recommended_next, next_commands: [.next_commands[].id], open_commands: [.open_commands[].id]}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_report.local_recall_repair_v1.json"
```

Inserted `Me` turns start as `needs_review`, so this profile is for inspection and explicit use until
review closes them.
The local-recall repair report also owns the next-step handoff; the CLI prints those JSON commands
after repair, with a fallback only for older reports.
For short islands near a parent boundary, `local_recall_repair_v1` also records raw micro-ASR rows
and may use `boundary_overlap_fallback` when Whisper recognized text but the row midpoint landed
just outside the selected local island. This is still conservative: the recovered turn is inserted
only into the explicit repair profile and remains `needs_review`.
After operational readiness is rebuilt, inserted repair turns appear in the `check_local_recall`
review lane as `local_recall_repair` rows. Unlike raw audit-only local-recall rows, these rows point
to actual `Me` utterance IDs in `local_recall_repair_v1`, so review can keep or drop the inserted
turn explicitly. `murmurmark review agent` can also close the narrow high-confidence case without
listening: the repair must be local-only by speaker-state, have successful micro-ASR, and pass the
agent confidence thresholds. Applied `keep_me`, `drop_me` and `skip` decisions are treated as closed
by operational readiness and do not reappear in `murmurmark next corpus`. Lower-confidence insertions
remain in `check_local_recall`.
For a corpus-level view of these candidate repairs:

```bash
murmurmark corpus local-recall-repair
murmurmark corpus local-recall-repair all --repair --no-synthesize

less sessions/_reports/local-recall-repair/local_recall_repair_corpus_report.md
```

When inserted repairs exist, the corpus report writes `next_commands`; the CLI prints the report path,
the first safe command as `recommended_next`, and repeats it as the final `next`. Complete sessions
go straight to review, for example:

```bash
murmurmark review lane check_local_recall --session ./sessions/<session>
```

If the inserted repair belongs to an incomplete session, the report prints `murmurmark process ...`
first and leaves the inserted turn out of the reviewable count until the session is complete.

`murmurmark corpus remote-leak` writes `sessions/_reports/remote-leak-segment/`. It aggregates
per-session remote-leak/remote-duplicate segment plans into one corpus queue and keeps the same
audit-only policy:
no transcript profile and no raw audio are modified. `murmurmark corpus process all` refreshes these
plans before session-quality and corpus aggregation. Use `corpus remote-leak --plan` when you only
want to refresh the remote-leak queue. The report writes `next_commands`: missing plans point to
`murmurmark corpus remote-leak --plan`, complete sessions with protected-local-content intervals
point to `murmurmark review lane check_unique_me_content --session ...`, and incomplete sessions
go back through `murmurmark process ...` first. The CLI prints `recommendation`, `read`,
`recommended_next` and final `next` so the queue is directly actionable from terminal output.
`corpus gate` reads this aggregate report and warns
when plans are missing or when protected-local-content leak intervals remain in the queue.
Corpus CLI commands keep per-session helper output quiet and show one aggregate handoff summary.
`murmurmark corpus report` prints the existing session-quality summary and, when the files already
exist, also prints short summaries for transcript-order, remote-leak, corpus-gates and
operational-readiness. It does not rebuild those reports; it is the quick “what is the current state?” command after a heavier
`murmurmark corpus process all`. The process command completes the refresh and prints summaries even
when corpus gates are currently `failed`; run `murmurmark corpus gate` separately when a strict
non-zero gate is useful for CI or release checks. Operational readiness points to a concrete
`murmurmark process sessions/<id>` target only for pipeline-incomplete sessions; complete risky
sessions remain review targets. It falls back to `murmurmark corpus process all` when no incomplete
target is known. Use
`murmurmark report corpus` when session-quality and operational-readiness need a refresh without
rebuilding heavier corpus diagnostics.
Operational readiness excludes obvious diagnostic/smoke sessions (`audio-input-*`, `*-talk-routed`,
`*-talk-audio-input`, `smoke`, `test`, `talk-solo`, `voice-processing-smoke`) and known-duration
captures shorter than 60 seconds from the working-meeting scope. The files remain in `sessions/` for
debugging, but they do not become next actions for the CLI MVP readiness loop.
Its `promotion_plan` section explains the current readiness delta: unresolved warnings, sessions not
ready for notes, remaining review minutes, and the next action class.
Its `Review Queue Strategy` section groups the remaining queue into lanes and shows the first useful
lane to close. Export-blocking lanes such as transcript order, local recall, or unique `Me` content
come first; `fast_confirm_drop` remains the quick lane when no blocking lane needs priority. The
report also estimates the queue that remains after the first lane. The strategy includes raw rows,
packed actions and grouped rows saved per lane; grouped rows are safe duplicates of the same review
decision, usually several checks tied to one `Me` utterance.
`build-review-plan.py` turns that queue into a compact checklist. Use it when a session is
`review_first`: listen to the listed stereo clips or local-recall mic snippets, decide whether each
`Me` candidate is leaked remote speech, real local speech, lost local speech, order-risk, or unclear, then keep
unclear cases marked for review.
The plan also assigns each row a `review_lane`: `fast_confirm_drop` for likely complete duplicate/noise
rows, `check_unique_me_content` for partial duplicates and leaks, `check_local_recall` for possible
missing local speech, `check_transcript_order` for chronology-risk rows, `confirm_benign` for likely
harmless overlap, and `classify_audio` for anything else.
For `check_transcript_order`, lane packs render short mic/remote clips around the crossed `Me` and
`Colleagues` utterances; the full `transcript_order_review.md` remains linked as text evidence.
Close the plan's `first_recommended_lane` first; it is chosen to reduce the largest blocking lane,
not just to pick the easiest audio. Keep the other lanes conservative unless the audio or chronology
evidence is clear.
`murmurmark review next "$SESSION"` is the quickest entry point for one session: it refreshes
`session_readiness.json`, shows gate/profile/verdict/review burden, builds a session-local review
plan if needed, then prints the next review commands. If the refreshed gate does not require review,
`review next` ignores any old session-local plan and points to `murmurmark next "$SESSION"` plus
`murmurmark status "$SESSION"` instead. `murmurmark review first-lane --session
"$SESSION"` now defaults to that session-local plan. The same is true for
`murmurmark review workspace --session "$SESSION"`, `murmurmark review workspace apply --session
"$SESSION"`, `murmurmark review progress --session "$SESSION"` and `murmurmark review apply --session
"$SESSION"`. Existing non-empty session-local plans are preserved, so a prepared explicit lane pack
is not overwritten by an empty refresh. When a plan exists for a review-required session, `review next`
prints `first_lane_flow` for the largest blocking lane,
`quick_lane_flow` for the fastest confirm/drop pass when that is a different lane, and
`workspace_flow` for reviewing all lanes. Each flow includes the build/listen and apply commands in
order. It also prints why the first lane was chosen, which lane is the fastest quick pass, and the
estimated queue after closing the first lane. When the local plan carries packed-action metrics,
`review next` prints `review_actions`, `grouped_review_rows` and `remaining_actions`, so this view
matches `murmurmark report corpus` instead of falling back to raw row counts only. It also prints
`open` commands for existing readiness/review-plan/progress reports, so the review entry point has
both the reading path and the action path in one terminal block. If `review_actions` is `0`, `review
next` prints `review_handoff: no_actionable_review_rows` and points to `status`/`report`/readiness
documents instead of recommending `review first-lane`; this means the queue is exhausted and the
remaining blocker is documented, not a hidden audio task. `review first-lane`,
`review lane apply`, `review workspace`, `review progress` and workspace apply also print a headline
`recommended_next`; `review next`, `review progress` and `review apply` also repeat the primary
handoff as the final copyable `next: ...` line. If `review progress` sees
that the current `next_lane` already has a lane pack, it recommends the prepared
`afplay`/`less`/`$EDITOR` handoff and lane apply commands instead of rebuilding that pack. When the
answer sheet has reviewed answers, it recommends lane apply `--dry-run` first. The
workspace command prints every lane pack with suggested compact answers and the
`afplay`/`$EDITOR` commands to use next, so normal review does not require opening
`review_workspace.json`. After a successful single-session apply, the CLI prints the refreshed readiness summary
so the next export or retention command is visible immediately. Use `murmurmark review first-lane`
or bare `murmurmark review progress` for the global corpus queue.
The review plan keeps both `raw_item_count` and `review_action_count`: raw items are source risks,
while actions are answer-sheet decisions after safe grouping by `Me` utterance. Same-`Me`
`check_unique_me_content` / `classify_audio` rows count as one action because one keep/drop/review
answer resolves the same local utterance.
`grouped_review_row_count` is the saved manual-action estimate.
`murmurmark review first-lane` refreshes the plan and builds the lane pack for that recommended lane.
With `--session`, its default paths are under `SESSION/derived/readiness/`; without `--session`, it
uses the global corpus queue under `sessions/_reports/`.
Use `murmurmark review lane check_local_recall --session "$SESSION"` to build one explicit lane pack,
for example when local-recall repair inserted short `Me` turns and the recommended first lane is
still `fast_confirm_drop`.
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
murmurmark review latest --lane fast_confirm_drop
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
murmurmark review workspace --session "$SESSION"

afplay "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav"
less "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md"
$EDITOR "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt"
```

The lane Markdown is a review artifact, not just an index. For each item it lists the allowed
decisions, suggested reason, utterance ids, selected audio command, transcript evidence and a
`Review focus` hint. The hint names the exact question to answer, such as "does `Me` contain unique
local speech outside the remote overlap?", and keeps unsafe lanes from looking like automatic
confirm/drop work. This is usually enough to review a lane from the Markdown and answer sheet without
opening the JSON manifest.

To prepare all remaining lanes at once:

```bash
murmurmark review workspace --session "$SESSION"
less "$SESSION/derived/readiness/review-plan/review_workspace.md"
```

Then edit the answer sheet for the lane. Dots mean "not reviewed yet"; replace only the items you
have actually checked:

```bash
$EDITOR "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt"
```

Each lane also has a suggested answer sheet. It is useful for comparison after listening:

```bash
less "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.suggested.txt"
```

Then copy decisions from the answer sheet back into the full review file:

```bash
murmurmark review lane apply first --session "$SESSION"
```

`first` resolves to `review_queue_strategy.first_recommended_lane` from the session-local
`review_plan.json`; pass an explicit lane name when you intentionally reviewed another lane. The
lane-pack command prints the suggested compact answer line, `afplay`, `$EDITOR`, dry-run and exact
`review lane apply ...` command for the generated answer sheet. It also prints `read: less ...` for
the lane Markdown, which is the fastest place to inspect allowed decisions, suggested reasons and
evidence text before editing the answer sheet.
For lanes such as `check_transcript_order`, `check_unique_me_content` and `classify_audio`, the pack
groups repeated risks for the same exact set of `Me` utterance ids. The answer sheet still has one
character per pack item, but a grouped item can apply that answer to several underlying review rows;
the Markdown shows the grouped row count and source audit ids. For mixed rows, the pack exposes only
the shared safe decision set, so a grouped item does not offer `drop_remote` unless every underlying
row allows it. When the CLI builds one `check_unique_me_content` or `classify_audio` lane, it also
includes open rows from the paired lane if they point to the same `Me` utterance. Workspace packs keep
lanes separate, so the same unresolved row does not appear in two answer sheets.
The generated answer sheet repeats the short focus as `focus=...` on every item comment, so a review
pass can stay inside the answer file after the first listen.
The CLI output prints both source `rows` and packed `items`, plus `grouped_rows_saved` when grouping
was applied.
It also prints `manual_flow`, optional `suggested_flow`, and `after_apply`, so a reviewer can follow
the terminal handoff through dry-run, apply and progress without opening this runbook. The lane-pack
JSON stores the same handoff as `recommended_next`, `next_commands`, `open_commands`, `manual_flow`,
`suggested_flow` and `after_apply`, so agents can continue from the manifest.
The JSON also stores SHA-256 fingerprints for the review template and existing decisions file. Corpus
handoff uses those fingerprints rather than report timestamps, so a harmless `murmurmark report
corpus` refresh does not hide an already prepared lane pack. `murmurmark next corpus --refresh`
rebuilds the first recommended lane pack after refreshing readiness, so the printed pack size does
not lag behind newly applied agent decisions.
It also prints `suggested_dry_run` and `suggested_apply`; these call
`review lane apply ... --answers-source suggested`, read
`review_lane_answers.<lane>.suggested.txt`, and are meant for explicit reviewer-approved use after a
dry run. `review lane apply --dry-run` writes `review_lane_pack_apply_report.json` and prints
`lane_result`, so you can see how many items would become reviewed or remain `todo` before writing
`review_decisions.jsonl`. The report stores `recommended_next`, `next_commands` and `open_commands`,
so scripts and agents can continue from the JSON report.
When rows remain `todo`, the dry run points back to the lane Markdown and answer sheet. When the dry
run would close rows, it prints the exact non-dry-run command under `next`. Applying the
lane refreshes `review_decisions_progress.json` and prints the next remaining lane before the
workspace fallback. When batch apply is
not ready yet, `review apply --session "$SESSION"` points to `next_lane` when progress exists, or to
`review first-lane` / `review lane apply first` when no decisions file exists yet. It keeps the
workspace flow as fallback instead of leaving the reviewer to inspect JSON. `review progress --session
"$SESSION"` prints row progress, packed review-action progress, `by_lane`, `next_lane`,
lane-specific build/apply commands, plus the workspace/apply/progress chain while rows remain. It
prints `review apply --session "$SESSION"` only when the checklist is ready for the batch apply.
If `review apply --session "$SESSION"` is called before `review_decisions.jsonl` exists, it prints
`status: not_ready`, the missing file and the first-lane/workspace/progress commands to run next.
If the decisions file exists but the checklist still has `todo` rows, it refreshes
`review_decisions_progress.json`, prints `reviewed`, `remaining`, packed review actions and
`by_lane`, then points back to workspace/workspace-apply/progress without running batch apply. After
a successful single-session batch apply, `review apply` uses the refreshed readiness `next_commands`
for its primary `recommended_next`, prints the full `next` list from
`review_decisions_apply_report.json`, and keeps `report_next` for an explicit status refresh.

The lower-level equivalent is still useful for debugging exact paths:

```bash
.venv/bin/python scripts/apply-review-lane-pack-decisions.py \
  "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json" \
  --template "$SESSION/derived/readiness/review-plan/review_decisions.template.jsonl" \
  --answers-file "$SESSION/derived/readiness/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt" \
  --out "$SESSION/derived/readiness/review-plan/review_decisions.jsonl"
```

If several lane answer sheets have been edited, apply the whole workspace instead:

```bash
murmurmark review workspace apply --session "$SESSION"
```

The apply output includes `lane_progress`. If rows are still `todo`, it prints the lane answer sheet
that still needs editing before `review apply` can safely run, plus the lane Markdown to read when
you need the evidence and allowed decisions again.

To estimate what the generated suggestions would do without writing decisions:

```bash
murmurmark review suggested "$SESSION"
```

To accept only those generated suggestions and leave all dots as manual review:

```bash
murmurmark review suggested apply "$SESSION"
```

This is the normal safe shortcut. It builds the workspace, refreshes lane suggestions from cached
local evidence and prints a `suggested_closure` block with before/after manual rows,
generated/actionable/needs-review counts, a conservative readiness projection, auto-closable rows and
the exact remaining manual queue by lane. If at least one row is safe, apply writes only those
reviewed suggested rows with partial apply enabled, preserves already closed rows that are no longer
in the new template, materializes `reviewed_v1` with `--allow-partial-review`, refreshes readiness
and keeps unresolved rows visible. It does not change capture, Echo Guard, ASR cache or raw CAF
tracks. If all generated suggested sheets contain only dots or `needs_review`, it writes no new
decisions and points to the first manual lane.

By default `review suggested` is cached-first: it reads existing `faster_whisper_judge.jsonl` and
Target-Me rows but does not start a long new faster-whisper decode. To compute a small targeted batch
inside the suggested flow intentionally:

```bash
MURMURMARK_TARGETED_JUDGE_COMPUTE=1 \
MURMURMARK_TARGETED_JUDGE_MAX_COMPUTED=4 \
murmurmark review suggested apply "$SESSION"
```

To refresh Target-Me evidence during suggested review intentionally:

```bash
MURMURMARK_REVIEW_TARGET_ME_REFRESH=1 murmurmark review suggested apply "$SESSION"
```

To audit the current live remaining mixed-boundary gap explicitly:

```bash
scripts/audit-live-local-recall-target-me.py "$SESSION" \
  --method resemblyzer_dvector \
  --include-remaining-gap \
  --fallback-persistent-profile \
  --max-items 120
```

This is diagnostic-only. It does not publish live `Me` text; it only writes
`derived/audit/live-local-recall-target-me/*` rows that `report-live-corpus-gates.py` can use to
explain whether the current blocker is missing voice coverage, weak voice evidence or insufficient
Target-Me enrollment. `--fallback-persistent-profile` is also diagnostic-only: it can copy
historical persistent Target-Me classifications into a session with insufficient same-session
enrollment, but those rows never create rescue policy candidates by themselves.

Target-Me now audits open readiness review-plan rows as well as older transcript/audit rows. The
audio-review pack stores those rows with `review_plan:*` reasons and `source_audit_id`, and
`target_me_audit.jsonl` echoes that ID in `source_audit_ids`. This lets review suggestions close a
lost-`Me`, local-recall, uncertain-audio or transcript-order row when the local speaker evidence
matches the same review queue item. In the 2026-07-01 corpus pass this closed two safe `keep_me`
rows and reduced the then-current mandatory queue from `7` actions / `11.19s` to `5` actions /
`8.78s`. After the 2026-07-02 corpus expansion the same mechanism still reports `0` pending safe
suggestions; the remaining queue is treated as irreducible until new local evidence appears.

The lower-level equivalent is:

```bash
murmurmark review workspace --session "$SESSION"
murmurmark review workspace apply --session "$SESSION" \
  --answers-source suggested \
  --allow-partial \
  --dry-run
```

`murmurmark review workspace` prints `manual_flow`, optional `suggested_flow`, `after_apply`, and
the same command as `suggested_dry_run` when suggested sheets are present. Dry-run still writes
`review_workspace_apply_report.json`, so the CLI can print the same summary, exact remaining rows and
next commands without changing `review_decisions.jsonl`. The apply report also stores
`recommended_next`, `next_commands` and `open_commands`, so automation can resume from the report
after manual review. `review workspace apply --allow-partial` writes reviewed suggested rows even
when some workspace answers are still `todo`; without `--allow-partial`, incomplete suggested sheets
remain preview-only. Incomplete manual review now points to the concrete answer sheet edit command
instead of asking you to rebuild the same lane pack.
`review_workspace_apply_report.json` stores the `suggested_closure` block even in dry-run mode.
That block is the source of truth for “what can be closed without listening” versus “what still
needs a human”. `review_workspace.json` stores the same handoff as `recommended_next`,
`next_commands`, `open_commands`, `manual_flow`, `suggested_flow` and `after_apply`, so agents can
continue from the workspace manifest.

To materialize those suggestions as a separate shadow transcript for comparison, write a suggested
decisions file and build `suggested_review_v1`:

```bash
murmurmark review workspace apply \
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

When audio-review finds partial `remote_duplicate` rows inside longer `Me` utterances, use the
segment-level cleanup profile:

```bash
murmurmark cleanup "$SESSION" \
  --input-profile agent_reviewed_v1 \
  --output-profile audit_cleanup_v7

murmurmark synthesize "$SESSION" --transcript-profile audit_cleanup_v7
```

`audit_cleanup_v7` removes only matched remote token spans from `Me`, writes replacement `Me`
segments with new ids, and treats the original audio-review rows as resolved only when their original
`Me` ids no longer exist in the selected profile. When v7 is built from `agent_reviewed_v1`, the
readiness reports also inherit the audio-review rows already closed by the input review profile; v7
adds only the new segment-level repair evidence. It does not modify raw capture, Echo Guard, ASR,
`shadow_v2`, earlier cleanup profiles or reviewed profiles.

The automatic agent-reviewed layer uses audio-review audit rows, the audio-judge queue and
high-confidence local-recall repair rows to close only items that are safe without listening. It
writes `agent_reviewed_v1`; this profile is selected by `auto` after `reviewed_v1` and before
automatic cleanup profiles when its gates pass.
It also clears the narrowest `uncertain` cases as `keep_me` when the row has no remote/error signal,
no remote utterance, near-full `Me` coverage and `speaker_state` says the interval is mostly
`local_only`. It also clears short local-only `asr_noise` labels as `keep_me`, adjacent `Me`
continuations, high-confidence local-recall repair insertions, and sibling rows for the same exact
`Me` utterance after another row has already confirmed that utterance as local speech. The raw
`local_recall_repair_v1` profile remains an intermediate artifact; after agent review, readiness
selects the confirmed `agent_reviewed_v1` profile.
It can also clear the narrow transcript-order audit case where a short remote backchannel such as
`Спасибо` is fully inside a long confirmed `Me` utterance, has no text overlap with it, and only
needs `keep_me` marking rather than text or timestamp repair.
When `faster_whisper_judge.jsonl` exists, the agent layer also consumes only its high-confidence
classes: `confirm_me` and `confirm_timing_or_doubletalk` can close a `remote_leak` row as `keep_me`;
`confirm_remote_duplicate` and `confirm_asr_noise` can drop a whole `Me` row only when the normal
duplicate/noise safety gates still pass. A confirmed `keep_me` can also propagate to sibling
`uncertain` rows for the same exact `Me` utterance.
Its report also explains the remaining queue with rejected-candidate aggregates:
`rejected_by_reason`, `rejected_by_label`, `rejected_by_verdict`, `rejected_by_reason_and_label` and
`top_rejected_reasons`. Use those fields to decide the next narrow automation rule. If a lane is still
listed by `murmurmark next corpus`, review that lane pack or add a rule only for the dominant rejected
pattern; avoid broad threshold changes.

```bash
murmurmark review agent
```

Use it as a medium-risk automation layer, not as proof that every remaining questionable phrase is
correct. `drop_me` is limited to clear whole-utterance duplicates/noise. `keep_me` closes review
burden for strong local-support rows, including narrow `remote_leak` cases where independent
speaker-state evidence says the overlap is actually local speech, and short duplicates where that
same evidence supports keeping the full `Me` utterance. Unresolved rows remain in `review_first`
sessions.

Answer shortcuts are `d=drop_me`, `c=drop_remote`, `k=keep_me`, `r` or `?=needs_review`, `s=skip`, and `.` or `n=todo`.
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
successful apply. It can drop whole reviewed `Me` utterances, drop reviewed `Colleagues` utterances
when remote contains a duplicate of local speech, clear review flags for confirmed local speech,
close checked local-recall rows, or keep an item marked `needs_review`.
Local-recall rows are audit-only: they do not add missing words to the transcript, and `drop_me` /
`drop_remote` are not valid decisions for them. Transcript-order rows are audit-only for timeline content: review can
clear or keep the chronology risk and records `quality.transcript_order_review` on affected
utterances, but it does not move utterances or edit text. `reviewed_v1` gates pass only when
the corresponding `review_decisions.template.jsonl` rows for that session are all closed with
an allowed `drop_me`, `drop_remote`, `keep_me`, `needs_review`, or `skip`; an invalid file is written for
audit but is not selected by `auto`. `--allow-partial-review` is the exception for agent-led cleanup:
closed rows are applied, `coverage.complete` stays `false`, and `partial_review_scope_allowed` plus
`review_scope_remaining_seconds` keep the session in `review_first` until the rest is resolved. The template includes `suggested_decision` hints, but they are
not applied until the reviewer explicitly edits `decision`. For `remote_duplicate`, those hints are coverage-aware:
if the duplicate covers only part of a longer `Me` utterance, the plan uses
`check_unique_me_content` and suggests `needs_review`, because `drop_me` would remove the whole
utterance. In that lane, `drop_remote` is available when the reviewed remote utterance is the actual
duplicate. It does not edit `audit_cleanup_v1/v2/v3/v4/v5/v6/v7`.

After closing a review file, prefer:

```bash
.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --allow-partial-review \
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
          mic/
            chunk_cache_report.json
            0001_000000s.json
            0001_000000s.meta.json
          remote/
            chunk_cache_report.json
        chunk_rebuild_check.json
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

The same `raw/chunks/<track>/` shape can also be materialized from eligible live ASR chunks by
`scripts/materialize-live-asr-cache.py`. In that case chunk metadata names
`source_audio.kind: live_asr_cache`; the safety check is not exact source WAV identity, but
`raw/chunk_rebuild_check.json` proving that top-level raw ASR JSON can be rebuilt from those
materialized chunks. If the live chunks are not compatible, the bridge writes `not_eligible` and
normal batch ASR runs.

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
- low-confidence remote candidates remain remote-only: the pipeline may use remote micro-ASR or mark
  the utterance for review, but it never copies recognized mic text into the `Colleagues` role;
- output generation enforces this provenance contract and fails instead of silently accepting a
  `Colleagues` row backed by mic text.

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
auto      -> promoted residual_local_recall_v1 when its corpus/session/hash gates pass; otherwise promoted residual_audio_arbitration_v1 when its gates pass; otherwise promoted residual_me_evidence_v1; otherwise promoted authoritative_boundary_v1; then audit_cleanup_v7 when it passed and applied material segment repair, reviewed_v1 when review gates pass, agent_reviewed_v1 when agent gates pass, audit_cleanup_v6/v5/v4/v3/v2/v1 with passing cleanup gates, a compatible passing order_repair_v1, passing shadow_v2, then current
current   -> baseline clean_dialogue.json
shadow_v2 -> shadow clean_dialogue.shadow_v2.json, marked risky if comparison failed
audit_cleanup_v1..v7 -> audit-cleaned dialogue, marked risky if cleanup gates failed
reviewed_v1 -> human-reviewed dialogue, marked risky if review gates failed
agent_reviewed_v1 -> agent-reviewed dialogue, marked risky if review gates failed
suggested_review_v1 -> machine-suggested review candidate, explicit only, never selected by auto
order_repair_v1 -> transcript-order repair candidate, eligible for auto only when built over the selected base profile, gates passed and at least one repair was applied; marked risky if requested explicitly and gates failed
authoritative_boundary_v1 -> frozen corpus boundary profile; eligible for auto only after global PROMOTE, per-session gates and promoted-session membership all pass
residual_me_evidence_v1 -> frozen residual evidence profile; eligible for auto only after global PROMOTE, per-session gates, promoted-session membership, frozen source hashes and output fingerprints all pass
residual_audio_arbitration_v1 -> frozen audio-review arbitration candidate; eligible for auto only after global PROMOTE, per-session gates, promoted-session membership, frozen source hashes and output fingerprints all pass; current corpus decision is DO_NOT_PROMOTE
residual_local_recall_v1 -> frozen local-recall closure profile; eligible for auto only after global PROMOTE, per-session and synthesis gates, promoted-session membership, frozen source hashes and output fingerprints all pass
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
features and the item is penalized before Markdown selection. The `murmurmark synthesize`,
`murmurmark notes` and `murmurmark transcript` command summaries print the remaining `review_items`
count and top review item types, so this is visible without opening JSON. When review work remains,
their primary next command points to `murmurmark review next`.

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
