# First Recording Runbook

Use this runbook to prove that a fresh machine can record a minimal MurmurMark session and prepare it for transcription.

## Preconditions

- Xcode command line tools are installed.
- `ffmpeg`, `ffprobe`, `jq`, `swiftlint` are available in `PATH`.
- The repository builds with `scripts/check.sh`.

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

If `doctor` reports `shareable displays: 0`, run MurmurMark from a logged-in desktop session and
re-check before a real meeting. The CLI may have permission but still be unable to build the system
capture filter when macOS does not expose a shareable display to the launching process.

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

This is the canonical v1 path for Echo Guard work: ScreenCaptureKit writes separate `audio/mic/000001.caf` and `audio/remote/000001.caf` tracks, and later preprocessing works algorithmically from those two tracks. Do not use BlackHole, Loopback or `--remote-backend audio-input` for normal Echo Guard tests.

Optional live-shadow recording:

```bash
murmurmark record --target-bundle system --live-pipeline --live-segment-sec 60 --live-overlap-sec 5
murmurmark status latest
murmurmark latest
SESSION="sessions/<printed-session>"
less "$SESSION/derived/live/transcript.draft.md"
murmurmark next latest
```

`--live-pipeline` duplicates closed mic/remote capture windows into `derived/live/audio/`, starts a
shadow worker and writes `derived/live/transcript.draft.md`,
`derived/live/live_pipeline_report.json` and `derived/live/chunks.jsonl`. After stop it runs the
normal batch-grade reconcile and writes `derived/live/final_reconcile_report.json`; if live ASR
cannot be safely reused yet, the report says `speedup_status: fallback_batch_asr`. The draft is not
the final transcript. If the worker crashes or falls behind, raw capture should still finish as a
normal session and can be processed by the batch pipeline. The derived segment writer is also
best-effort: if it fails, MurmurMark disables live segments with a warning instead of stopping raw
recording. Use `--live-no-finalize` when you only want to test the live draft and run
`murmurmark process` manually.

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
murmurmark corpus live all
less sessions/_reports/live-pipeline/live_corpus_gates_report.md
```

The correct v1 outcome is normally `shadow_only_not_promotable`; promotion requires future gates for
order risk, local recall, remote leakage, review burden and boundary speech. The current comparison
already measures the first practical version of those checks from live chunks versus the selected
batch transcript, but `promotion_allowed` must remain `false` until real live-session coverage is
broad enough and the corpus gates are intentionally promoted. The command prints `recommended_next`
and `next:` lines; use them to inspect the current blocker or start the next live sample. The
Markdown report's `Gate Issues` section lists the exact non-passing gates by session.

For the current live-parity coverage goal, use the strict form after recording at least one real
live session:

```bash
murmurmark corpus live all \
  --min-live-sessions 1 \
  --min-compared-sessions 1 \
  --min-meaningful-compared-sessions 1 \
  --min-passing-compared-sessions 1 \
  --max-order-mismatches 0 \
  --max-missing-me-sec 0 \
  --max-remote-in-me-sec 0 \
  --max-boundary-duplicates 0 \
  --require-passing-gates \
  --fail-on-promotion
```

This command should fail whenever coverage is missing or any live parity gate is still warning,
failed or not evaluated. A failure is useful evidence: it says why live chunks are not ready to be a
trusted cache source.

Without `--duration`, recording continues until `Ctrl-C`. MurmurMark catches that explicit stop,
stops capture, closes audio files and writes `session.json`.
If ScreenCaptureKit stops before `Ctrl-C`, MurmurMark tries to restart the capture stream and keep
recording into the same session. A successful restart is written to `events.jsonl` as
`capture.restarted`. If restart fails or capture keeps producing no audio, MurmurMark finalizes the
partial session, writes `session.json` with `status: partial`, records `health.partial: true`,
records `capture.stopped` with `partial: true`, and exits with an error. `SIGTERM` and `SIGHUP` are
also treated as unexpected stops rather than successful meeting ends.

Do not process a partial session as a complete meeting; inspect it or start a new recording.
`murmurmark status SESSION` and `murmurmark next SESSION` point to `murmurmark inspect SESSION` when
readiness has not been generated yet. The normal `murmurmark process` path refuses unrecovered
interrupted partial captures unless `--allow-partial` is passed explicitly for debugging.

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
