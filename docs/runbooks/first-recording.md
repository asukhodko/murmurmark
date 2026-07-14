# First Recording Runbook

Use this runbook to prove that a fresh machine can record a minimal MurmurMark session and prepare it for transcription.

## Preconditions

- Xcode command line tools are installed.
- `ffmpeg`, `ffprobe`, `jq`, `swiftlint` are available in `PATH`.
- The repository builds with `scripts/check.sh`.
- Before changing capture code, run `MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh`.

## Permission Check

Run:

```bash
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"
murmurmark doctor
murmurmark self-test
murmurmark config init
murmurmark acceptance --skip-release
murmurmark config print
```

To verify the release layout instead of the working checkout:

```bash
scripts/build-release-bundle.sh --verify
BUNDLE="$(find dist/release-bundles -maxdepth 1 -type d -name 'murmurmark-*' | sort | tail -1)"
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" doctor --strict
```

Expected before permissions are granted:

- `ffmpeg`, `ffprobe`, `whisper-cli`, Python and required Python modules are found;
- the configured whisper.cpp model exists;
- `microphone permission` is `ok` or clearly reports the missing state;
- `screen/system audio permission` is `ok` or clearly reports that access is missing;
- `shareable displays` is not zero for the normal `record --target-bundle system` path.
- `readiness` is `ok` or `usable_with_warnings` when only optional checks are missing.

If screen or system audio access is missing, grant it in macOS privacy settings for the terminal application or Codex app that launches the CLI. On current macOS versions the setting may appear as either `Screen & System Audio Recording` or screen recording/system audio capture wording.

If microphone access is missing, grant microphone access to the same launching application.

Re-run `doctor` after changing permissions. It prints `murmurmark self-test` and the next normal CLI
commands when the machine is usable. Use `murmurmark doctor --strict` when a setup script should fail
on missing required dependencies.

If `doctor` reports `shareable displays: 0`, run MurmurMark from a logged-in desktop session with an
awake display and re-check before a real meeting. The CLI may have permission but still be unable to
build the system capture filter when macOS does not expose a shareable display to the launching
process. A practical check is:

```bash
caffeinate -u -t 5
murmurmark doctor --strict
```

Use `caffeinate -dimsu ...` for long verification commands such as `scripts/check.sh` when the
machine may otherwise dim or sleep during the run.

## Short Recording

Run:

```bash
rm -rf ./sessions/smoke
murmurmark record --out ./sessions/smoke --duration 5 --target-bundle system
murmurmark inspect ./sessions/smoke
murmurmark export-audio ./sessions/smoke
```

The session is acceptable only if:

- `audio/mic/000001.caf` exists and is non-empty;
- `audio/remote/000001.caf` exists and is non-empty;
- `session.json`, `events.jsonl` and `pipeline_job.json` exist;
- `inspect` reports one mic file and one remote file;
- `derived/asr/mic.wav` is `16000 Hz`, mono and non-empty;
- `derived/asr/remote.wav` is `16000 Hz`, mono and non-empty.

Useful checks:

```bash
find ./sessions/smoke -maxdepth 3 -type f -print
ffprobe -v error -show_entries stream=sample_rate,channels -of compact=p=0:nk=1 ./sessions/smoke/derived/asr/mic.wav
ffprobe -v error -show_entries stream=sample_rate,channels -of compact=p=0:nk=1 ./sessions/smoke/derived/asr/remote.wav
```

When testing a real call without headphones, the microphone track may also contain remote participants through speaker bleed. This is expected at the capture layer: MurmurMark records what reaches the selected microphone before any later echo cleanup or source separation.

## Real Task Recording

For real meetings, install the local wrapper once and then use the `murmurmark` command:

```bash
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"
murmurmark doctor
murmurmark self-test
murmurmark config init
murmurmark acceptance --live-checklist

SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
murmurmark inspect "$SESSION"
```

`acceptance --live-checklist` intentionally prints two gates. Use `live_recording_gate` for normal
real meetings. Treat `near_realtime_shadow_gate` as shadow evidence collection only: it starts with
`MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh` and must keep live
promotion blocked until live-vs-batch parity evidence is explicitly approved.
For a short lab run after the safety probe, use the normal recorder with the committed-PCM
experiment sidecar:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live-evidence"
murmurmark record --out "$SESSION" --target-bundle system --duration 120 --experiment live-shadow-v1
# In a second terminal while recording:
murmurmark live watch "$SESSION"
# After recording stops:
murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
murmurmark live evidence "$SESSION"
murmurmark live replay "$SESSION" --refresh
```

This command still writes the normal raw CAF session first. The live draft is shadow evidence and
must not be treated as final transcript while parity gates are red.

Near-realtime evidence has two paths. The old inline `--live-pipeline` path remains unsafe/lab-only
and must not be used for valuable meetings. The controlled experiment path keeps the normal raw
writer as the only source of truth and lets the sidecar consume committed PCM after raw write:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live-lab"
murmurmark record --out "$SESSION" --target-bundle system --duration 120 --experiment live-shadow-v1
murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

The runtime shape is:

```text
capture -> durable raw writer -> stable session
                    |
                    +-> nonblocking committed PCM queue -> live segmenter -> live ASR draft
```

The sidecar writes closed segment files into `derived/experiments/live-shadow-v1/audio/` and keeps
compatibility rows in `derived/live/segments.jsonl`. `derived/live/transcript.preview.md` updates
during the meeting and keeps only causal Target-Me candidates accepted by the remote-energy gate.
The fuller `derived/live/transcript.draft.md` is diagnostic evidence and may include rejected
candidate-only text. The batch transcript from raw CAF remains authoritative. The raw commit log is
still written as evidence and as a post-stop fallback; preview never reads still-open CAF files.

Watch the conservative preview from a second terminal with `murmurmark live watch "$SESSION"`.
Start it after the recorder has printed the session directory and pass that explicit path: during
recording the final `session.json` does not exist yet, therefore `latest` is not usable until
finalization. Use `--diagnostic-draft` only when investigating all candidate evidence. Both modes
show worker heartbeat and lag; they must make a stalled preview visible while raw capture continues.
`murmurmark live evidence "$SESSION"` additionally checks
`derived/live/preview_snapshots.jsonl`: at least one non-empty snapshot must predate `ended_at` and
carry `recording_time_committed_pcm`. This is the proof that the visible preview was not rebuilt by
post-stop replay.

`murmurmark experiment compare ...` reads existing realtime artifacts only. If the recording-time
worker timed out and a diagnostic post-stop draft is useful, run
`murmurmark experiment recover-draft ...` explicitly. Recovery writes a separate fallback namespace
and cannot satisfy pre-stop parity gates.
`murmurmark live evidence "$SESSION"` is the per-session acceptance view. It writes a compact JSON
and Markdown verdict and distinguishes transport evidence from full transcript parity. After three
fresh sessions, refresh the corpus once with `murmurmark corpus live all --refresh`.
Use `murmurmark live replay "$SESSION" --refresh` to iterate without another recording. It writes a
policy matrix under `derived/live/replay-lab/`, keeps batch authoritative and accepts a candidate
only when local recall improves without extra remote leakage or blocking order errors. The same
report records why the current live chunk geometry can or cannot seed the batch ASR cache.
The default comparison computes the required parity gates and a paired direct comparison between
`online_live_me_remote_overlap_filter_v1` and `live_runtime_causal_target_me_direct_v1`.
For an additional focused shadow experiment, materialize the selected policy explicitly:

```bash
POLICY=online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_voice_activity_token_density_target_me_remote_gap_trim_micro_asr_v1
.venv/bin/python scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source target-me-remote-gap \
  --source-scope live
scripts/compare-live-batch.py "$SESSION" --lab-policy "$POLICY"
```

`--lab-policy` is repeatable. Keep `--with-labs` for deliberate full laboratory sweeps; it runs all
exploratory profiles and can take many minutes on a long meeting.

Normal `process` keeps the stronger-audio-judge queue broad but bounded: cheap cleanup runs first,
the review pack is rebuilt from the residual transcript, and the judge decodes `mic_clean + remote`.
Use `--stronger-audio-judge-exhaustive` only when all four clip sources are needed for diagnosis.

The current profile first trims strongly confirmed Target-Me spans to gaps between guarded live
remote turns, then runs short-window micro-ASR only for compact weak-text gaps. It publishes a
micro-ASR replacement only with local-source support and low remote similarity, and rejects text
already covered by a base `Me` turn. The generated draft is still shadow evidence; use the batch
transcript for notes and export.

The live worker now runs the causal past-only local-speaker path directly. It evaluates each chunk
using positive and remote-negative seeds from closed earlier chunks, then enrolls the current chunk.
Focused micro-ASR is limited to unpublished groups from chunk-level suppressed mic chunks. It first
uses remote-free gaps and then, only as a fallback, speaker-confirmed sliding windows. Inspect
its state and accepted/rejected candidates with:

```bash
jq '.' "$SESSION/derived/live/causal-target-me/state.json"
less "$SESSION/derived/live/causal-target-me/candidates.jsonl"
jq '.temporal_provenance, [.parity_gates.gates[] | select(.name | startswith("pre_stop"))]' \
  "$SESSION/derived/live/live_batch_comparison.json"
```

Replay the same causal logic over existing closed live chunks without touching raw capture or batch:

```bash
.venv/bin/python scripts/live-progressive-target-me.py "$SESSION"
.venv/bin/python scripts/compare-live-batch.py "$SESSION"
```

The runtime profile remains shadow-only. Corpus parity, not candidate count, decides whether it
improves local recall without increasing remote leakage or order risk.

Existing experiment sessions can still be analyzed without starting capture:

```bash
SESSION="sessions/<session-id>"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

Use direct `record --experiment live-shadow-v1` for the current committed-PCM controlled evidence
path. `murmurmark live pilot --controlled-real` still guards the older pilot workflow with its
explicit unsafe escape hatch; keep that command for compatibility, not as the normal recorder.

If recording finished but post-stop processing was interrupted, resume the same evidence collection
without starting another recording:

```bash
SESSION="sessions/<session-id>"
murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

This is the canonical v1 path for Echo Guard work: ScreenCaptureKit writes separate `audio/mic/000001.caf` and `audio/remote/000001.caf` tracks, and later preprocessing works algorithmically from those two tracks. Do not use BlackHole, Loopback or `--remote-backend audio-input` for normal Echo Guard tests.

Legacy live-shadow recording is disabled by default:

```bash
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
  murmurmark record --target-bundle system --live-pipeline --live-segment-sec 60 --live-overlap-sec 5
```

Do not use this as the normal meeting command. Older inline live segment writing could starve
ScreenCaptureKit audio delivery, which can leave the raw `mic` and `remote` tracks mostly silent.
Use `--experiment live-shadow-v1` for controlled sidecar evidence and the plain recording command
for production.

The supported experiment contract lives outside `derived/live`:

```text
derived/experiments/live-shadow-v1/
  experiment_manifest.json
  state.json
  events.jsonl
  report.json
  report.md
```

`derived/live` is compatibility storage for the current draft/chunk implementation. The experiment
contract is the stable status surface. It records `batch_authoritative: true`,
`promotion_allowed: false`, `raw_capture_affected`, recovery and comparison commands, raw seconds,
sidecar seconds and backpressure/disabled state. Check it after a pilot or failed sidecar run:

```bash
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

After batch processing, `derived/live/live_parity_session_report.md` explains whether the session can
count as a passing live comparison and lists the exact non-passing gates.

`murmurmark status "$SESSION"` prints the live worker, stage, captured/preprocessed/asr seconds, lag,
chunk count and final-reconcile status when these artifacts exist.

Each live segment has two timelines:

- `start_sec..end_sec`: the non-overlapping hard window that may be published for this segment.
- `clip_start_sec..clip_end_sec`: the copied audio clip passed to ASR, including overlap context
  before and after the hard window.

The default `--live-overlap-sec 5` is deliberately small: it gives Whisper boundary context without
letting adjacent segments publish the same words twice.

The final reconcile also writes `derived/live/live_asr_cache_report.json`. Check it when live mode
does not speed up post-meeting processing:

```bash
jq '.status, .reasons' "$SESSION/derived/live/live_asr_cache_report.json"
```

Common v1 reasons include `live_report_missing`, `raw_cache_already_exists`,
`segment_count_mismatch`, `asr_json_missing:remote:1`, `audio_prep_mismatch:mic:1`,
`window_duration_mismatch:1` and `overlap_after_mismatch:1`. They mean the live draft may still be
useful for orientation, but the final transcript should use normal batch ASR.

For a corpus-level live promotion check:

```bash
murmurmark corpus live all --refresh
less sessions/_reports/live-pipeline/live_corpus_gates_report.md
jq '.real_blocker_triage_summary' sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

`--refresh` reruns `compare-live-batch.py` from existing derived live/batch artifacts before
aggregation, so the corpus report does not use stale gate JSON after live-parity logic changes.
It does not modify raw capture or the authoritative batch transcript.

To refresh one materialized profile instead of every laboratory profile:

```bash
POLICY=online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_voice_activity_token_density_target_me_remote_gap_trim_micro_asr_v1
.venv/bin/python scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source target-me-remote-gap \
  --source-scope live
scripts/report-live-corpus-gates.py all --refresh --refresh-lab-policy "$POLICY"
```

The correct v1 outcome is normally `shadow_only_not_promotable`; promotion requires future gates for
capture safety, order risk, local recall, remote leakage, review burden and boundary speech. The
current comparison already measures the first practical version of those checks from live chunks
versus the selected batch transcript, including source-level `live_boundary_gate` suppression at
chunk edges. Boundary suppression now distinguishes safe resolved duplicates from unresolved
suppression with possible unique-word loss; only unresolved boundary suppression blocks the
chunk-boundary gate. Even so,
`promotion_allowed` must remain `false` until real
live-session coverage is broad enough and the corpus gates are intentionally promoted. While live is
quarantined, the report is evidence only. The command prints `recommended_next` and `next:` lines;
follow them only when they point to controlled pilot collection after full fail-open proof, or use
them to inspect the current blocker and return to the normal non-live `record -> process` path.
The Markdown report's
`Gate Issues` section lists the exact non-passing gates by session. `real_parity_dimensions` is the
promotion scope and counts only date-named real meeting sessions; diagnostic `_debug_*` and
`live-pilot-*` sessions stay visible for failure analysis but cannot satisfy real coverage.
After the full capture fail-open proof passes, also check `capture_safe_candidate_scope` and
`real_capture_safe_candidate_parity_dimensions`. This narrower slice includes only real,
meaningful, compared sessions whose capture safety and required artifacts already passed. It helps
separate remaining live parity blockers from older broken-capture evidence. It is still shadow-only:
`new_real_live_collection_allowed` must remain false until live promotion is explicitly approved.
If the full proof has passed, `controlled_real_live_pilot_allowed` may be true. That means a
future controlled Live Evidence run would be allowed only after capture isolation is proven again.
For now the runner refuses to start a new real live recording without
`--allow-unsafe-controlled-real-recording`; do not use that escape hatch for valuable meetings.
Use `next_focus` to see whether the next step is a candidate blocker or lab evidence.
`Parity Dimensions` keeps the full mixed audit view by capture safety, order risk, local recall,
remote leakage, review burden, notes readiness and chunk-boundary risk. `draft_text_recall` is
separate from `required_artifacts`: present live files are not enough if live draft text no longer
matches the authoritative batch transcript.
Start with `real_blocker_triage_summary` when deciding the next action. It groups real-session
blockers into actionable buckets such as batch review/readiness, missing artifacts, capture safety
risk, local recall gap, remote leakage and live draft drift. Treat it as diagnosis only: it does not
permit promotion or normal production live use.

Order/role reconciliation has a separate compact audit. Refresh the corpus report, then inspect the
effective blocker result:

```bash
murmurmark corpus live all --refresh
jq '.live_order_role_reconciliation' \
  sessions/_reports/live-pipeline/live_corpus_gates_report.json
less sessions/_reports/live-pipeline/live_order_role_reconciliation_v1.md
```

The 2026-07-14 seven-session scope classifies all `23` order rows and resolves the `15` previous
effective blockers without changing live turns. Raw matcher counters remain visible. A passing
effective order gate is therefore evidence that matcher/reference ambiguity no longer blocks the
candidate; it is not live-promotion approval. Local recall, remote leakage, review burden, notes
readiness and chunk-boundary gates still apply.
`objective_audit` is the compact checklist for the active near-realtime goal: real live sessions,
live-vs-batch comparison, required dimensions, batch-authoritative policy and promotion/collection
blocks. During quarantine it should keep `ready_for_live_promotion` and
`new_real_live_collection_allowed` false. `controlled_real_live_pilot_allowed` is a separate
evidence-collection flag and does not make live output authoritative.
It also checks `sessions/_reports/capture-regression/capture_regression_check.json`. A static
capture regression report proves the code shape and fixtures, but it cannot create live promotion
safety proof by itself. If full proof already exists, a later static check preserves it instead of
downgrading the operator state. Before any controlled Live Evidence run is allowed, run the full
local proof at least once:

```bash
MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
```

The required proof status is `full_fail_open_proof_passed`; `static_only` keeps live collection
blocked.
If `capture_safety` is blocking, `objective_audit.next_focus` should be
`capture_safe_redesign_before_more_live_coverage`; do not treat missing passing coverage as a prompt
to collect more real live sessions.
In quarantine, `recommended_next` and `next:` should lead to triage, inspection or a controlled pilot
preflight. They should not print raw `record --live-pipeline` as the next action.

For live-parity promotion diagnostics, use the explicit strict gate:

```bash
murmurmark live gate
```

It refreshes the corpus report and exits non-zero while live capture is quarantined or until enough
real sessions have meaningful passing comparisons. Under the hood it runs:

```bash
murmurmark corpus live all --refresh \
  --min-live-sessions 3 \
  --min-compared-sessions 3 \
  --min-meaningful-compared-sessions 3 \
  --min-passing-compared-sessions 3 \
  --max-order-mismatches 0 \
  --max-missing-me-sec 0 \
  --max-remote-in-me-sec 0 \
  --max-boundary-duplicates 0 \
  --require-passing-gates \
  --fail-on-promotion
```

This command should fail whenever coverage is missing or any live parity gate is still warning,
failed or not evaluated. Do not satisfy it by hand-running `record --live-pipeline`; use
`murmurmark live pilot --controlled-real` only when `murmurmark live status` says controlled evidence
collection is allowed. Batch output remains authoritative until the gate and promotion policy pass.

Without `--duration`, recording continues until `Ctrl-C`. MurmurMark catches that explicit stop,
stops capture, closes audio files and writes `session.json`.

Run only one `murmurmark record` process at a time. Do not start a safe recording in one terminal and
a live recording in another terminal for the same call: ScreenCaptureKit may hang before startup or
leave sessions unfinalized. MurmurMark keeps a recording lock and rejects a second concurrent
`record` before it creates another broken session. For live-vs-batch evidence, record once through
`murmurmark live pilot --controlled-real`; the runner processes the same raw CAF files through the
stable batch pipeline after recording stops.
If ScreenCaptureKit stops before `Ctrl-C`, MurmurMark tries to restart the capture stream and keep
recording into the same session. A successful restart is written to `events.jsonl` as
`capture.restarted`.

ScreenCaptureKit can omit audio buffers during silence or source inactivity. MurmurMark preserves
the wall-clock timeline by inserting silence for timestamp gaps in the raw CAF tracks. If no
ScreenCaptureKit audio samples arrive at the start of recording, MurmurMark tries short restarts and
then finalizes the session as partial instead of waiting through a whole meeting. If restart fails
after a real stream stop, if the final written CAF tracks cover far less time than the wall-clock
recording, if both mic and remote tracks are effectively silent, or if a long recording contains only
sparse audio bursts, MurmurMark writes explicit health warnings and blocks normal processing.
`SIGTERM` and `SIGHUP` are also treated as unexpected stops rather than successful meeting ends.

The `sparse_capture` blocker means the raw CAF files may be long, but there is too little active
audio for a meeting transcript. This usually points to ScreenCaptureKit not delivering useful audio
for most of the session. Do not trust old derived transcripts from such a session.

Do not process a partial or all-silent session as a complete meeting; inspect it or start a new
recording. `murmurmark status SESSION` and `murmurmark next SESSION` point to
`murmurmark inspect SESSION` for these cases. The normal `murmurmark process` path refuses
unrecovered interrupted or silent captures unless `--allow-partial` is passed explicitly for
debugging.

Without `--out`, MurmurMark creates a fresh directory under `./sessions`, for example:

```text
recording until Ctrl-C -> ./sessions/<session>
```

Use that printed path for processing and export. For real meetings, the safer pattern is to set
`SESSION` before recording and pass it with `--out "$SESSION"`; then every later command uses the
same variable. If the terminal scrollback is gone, `murmurmark latest` prints a copyable
`SESSION="..."` assignment, but `latest` is unsafe when another terminal may have started a newer
session.

```bash
murmurmark process "$SESSION"
murmurmark status "$SESSION"
murmurmark next "$SESSION"
murmurmark acceptance --live-session "$SESSION" --report /tmp/murmurmark-live-session.json

# If readiness says review_first, follow the printed review command first.
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark finish "$SESSION"
```

`--mic-backend voice-processing` and `--remote-backend audio-input` are experimental comparison modes. They are not the main product path and should not be used to judge the algorithmic subtraction problem unless the test explicitly says so.

For the normal path, expected `session.json` values are:

```json
{
  "capture_mode": "screencapturekit_system",
  "remote_audio": { "backend": "screencapturekit_audio" },
  "mic_audio": { "backend": "screencapturekit_microphone" }
}
```

For low-level capture or Echo Guard debugging, inspect the raw and derived audio explicitly:

```bash
murmurmark inspect "$SESSION"
murmurmark preprocess "$SESSION" --echo diagnostic
murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
murmurmark inspect "$SESSION" --echo
murmurmark export-audio "$SESSION"
afplay "$SESSION/audio/mic/000001.caf"
afplay "$SESSION/audio/remote/000001.caf"
afplay "$SESSION/derived/preprocess/audio/mic_role_preview.wav"
afplay "$SESSION/derived/preprocess/audio/mic_role_masked_for_asr.wav"
afplay "$SESSION/derived/asr/mic.wav"
afplay "$SESSION/derived/asr/remote.wav"
```

If Echo Guard has already run, `derived/asr/mic.wav` is exported from
`derived/preprocess/audio/mic_for_asr.wav`. If cleanup was rejected, `mic_for_asr.wav` is the
prepared raw mic fallback.

If you need a fixed directory for a planned test, pass `--out` explicitly:

```bash
murmurmark record --out ./sessions/my-test --target-bundle system
```

MurmurMark refuses to write into a non-empty output directory.

Echo diagnostics are optional for the minimal capture workflow. They are useful when recording without headphones and you want to know whether remote audio probably leaked into the microphone track.

`local_fir` is the current recommended Echo Guard cleanup engine for real speaker-bleed sessions. It keeps raw CAF files untouched, writes a diagnostic clean mic, writes a role-selected mic for ASR, and keeps raw mic as fallback if the quality gate rejects the candidate. `linear_baseline`, `speexdsp` and `webrtc-apm` remain comparison engines. `speexdsp` requires Homebrew packages `pkgconf` and `speexdsp` when the helper has not been built yet. `webrtc-apm` requires `rust`, `meson`, `ninja`, `cmake` and `abseil` when the helper has not been built yet.

## Known Limitation

The current CLI uses ScreenCaptureKit as the working capture backend. The target Core Audio Process
Tap design remains documented in ADR-0001 and ADR-0008 as a future option for more precise
per-application capture.
