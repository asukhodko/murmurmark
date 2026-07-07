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
murmurmark record --target-bundle system
murmurmark inspect latest
```

`acceptance --live-checklist` intentionally prints two gates. Use `live_recording_gate` for normal
real meetings. Treat `near_realtime_shadow_gate` as shadow evidence collection only: it starts with
`MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh` and must keep live
promotion blocked until live-vs-batch parity evidence is explicitly approved.
For a short lab pilot after the safety probe, run:

```bash
murmurmark live pilot --duration 45
```

The pilot refuses to create a new live recording unless
`sessions/_reports/capture-regression/capture_regression_check.json` says
`capture_safe_proof.status == "full_fail_open_proof_passed"`. `--skip-safety-gate` can reuse that
existing proof, but it does not bypass the proof requirement.

For a non-critical real pilot after the report says `controlled_real_live_pilot_allowed: true`, run:

```bash
murmurmark live status
murmurmark live gate
murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only
murmurmark live pilot --controlled-real --skip-safety-gate
```

The preflight command performs the same proof and corpus-gate checks without starting capture. The
pilot command records until `Ctrl-C`, runs normal batch processing after stop and refreshes
`murmurmark corpus live all --refresh`. Before recording, the runner refreshes the same corpus gates
and refuses to start if controlled real collection is no longer the safe next step. It is still
evidence collection: live output remains shadow-only and the batch transcript remains authoritative.
After processing, read `derived/live/live_parity_pilot_report.json`; `pilot_verdict`,
`contributes_to_passing_coverage` and `coverage_after.passing_compared_sessions_remaining` tell
whether this pilot reduced the remaining coverage target.

If recording finished but post-stop processing was interrupted, resume the same evidence collection
without starting another recording:

```bash
SESSION="sessions/<session-id>"
murmurmark live pilot "$SESSION" --controlled-real
```

This is the canonical v1 path for Echo Guard work: ScreenCaptureKit writes separate `audio/mic/000001.caf` and `audio/remote/000001.caf` tracks, and later preprocessing works algorithmically from those two tracks. Do not use BlackHole, Loopback or `--remote-backend audio-input` for normal Echo Guard tests.

Experimental live-shadow recording is disabled by default:

```bash
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
  murmurmark record --target-bundle system --live-pipeline --live-segment-sec 60 --live-overlap-sec 5
```

Do not use this as the normal meeting command. Older inline live segment writing could starve
ScreenCaptureKit audio delivery, which can leave the raw `mic` and `remote` tracks mostly silent.
The current async bounded live queue is still shadow-only until live-vs-batch parity gates pass. The
supported production path is the plain recording command above plus
`murmurmark process latest`.

`--live-pipeline` duplicates closed mic/remote capture windows into `derived/live/audio/`, starts a
shadow worker and writes `derived/live/transcript.draft.md`,
`derived/live/live_pipeline_report.json` and `derived/live/chunks.jsonl`. After stop it runs the
normal batch-grade reconcile and writes `derived/live/final_reconcile_report.json`; if live ASR
cannot be safely reused yet, the report says `speedup_status: fallback_batch_asr`. The draft is not
the final transcript. If the worker crashes or falls behind, raw capture should still finish as a
normal session and can be processed by the batch pipeline. After `Ctrl-C`, MurmurMark waits only a
short finalization tail for the live worker; a stuck worker is terminated and batch reconcile
continues. The derived segment writer is also best-effort: if it fails, MurmurMark disables live
segments with a warning instead of stopping raw recording. Use `--live-no-finalize` when you only
want to test the live draft and run `murmurmark process` manually.
After batch processing, `derived/live/live_parity_session_report.md` explains whether the session can
count as a passing live comparison and lists the exact non-passing gates.

`murmurmark status latest` prints the live worker, stage, captured/preprocessed/asr seconds, lag,
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
If the full proof has passed, `controlled_real_live_pilot_allowed` may be true. That means only a
non-critical live-pipeline pilot may be recorded to gather parity evidence, followed by normal batch
processing and `murmurmark corpus live all --refresh`. Use
`murmurmark live pilot --controlled-real --skip-safety-gate` instead of assembling
`record --live-pipeline` by hand; the runner rechecks the current live corpus gates before it starts
recording. Use `next_focus` to see whether the next step is a candidate blocker or a controlled
pilot.
`Parity Dimensions` keeps the full mixed audit view by capture safety, order risk, local recall,
remote leakage, review burden, notes readiness and chunk-boundary risk. `draft_text_recall` is
separate from `required_artifacts`: present live files are not enough if live draft text no longer
matches the authoritative batch transcript.
Start with `real_blocker_triage_summary` when deciding the next action. It groups real-session
blockers into actionable buckets such as batch review/readiness, missing artifacts, capture safety
risk, local recall gap, remote leakage and live draft drift. Treat it as diagnosis only: it does not
permit promotion or normal production live use.
`objective_audit` is the compact checklist for the active near-realtime goal: real live sessions,
live-vs-batch comparison, required dimensions, batch-authoritative policy and promotion/collection
blocks. During quarantine it should keep `ready_for_live_promotion` and
`new_real_live_collection_allowed` false. `controlled_real_live_pilot_allowed` is a separate
evidence-collection flag and does not make live output authoritative.
It also checks `sessions/_reports/capture-regression/capture_regression_check.json`. A static
capture regression report proves the code shape and fixtures, but not live promotion safety. Before
any controlled real live pilot is allowed, run the full local proof:

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

Use that printed path for processing and export. If the terminal scrollback is gone,
`murmurmark latest` prints a copyable `SESSION="..."` assignment. For normal work, `latest` is also
safe when the newest session is the one you just recorded.

```bash
murmurmark process latest
murmurmark status latest
murmurmark next latest
murmurmark acceptance --live-session latest --report /tmp/murmurmark-live-session.json

# If readiness says review_first, follow the printed review command first.
murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
murmurmark finish latest
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
