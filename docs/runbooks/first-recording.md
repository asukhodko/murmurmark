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
```

To verify the release layout instead of the working checkout:

```bash
scripts/build-release-bundle.sh
BUNDLE="$(find dist/release-bundles -maxdepth 1 -type d -name 'murmurmark-*' | sort | tail -1)"
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" doctor --strict
```

Expected before permissions are granted:

- `ffmpeg`, `ffprobe`, `whisper-cli`, Python and required Python modules are found;
- the configured whisper.cpp model exists;
- `microphone permission` is `ok` or clearly reports the missing state;
- `screen/system audio permission` is `ok` or clearly reports that access is missing.
- `readiness` is `ok` or `usable_with_warnings` when only optional checks are missing.

If screen or system audio access is missing, grant it in macOS privacy settings for the terminal application or Codex app that launches the CLI. On current macOS versions the setting may appear as either `Screen & System Audio Recording` or screen recording/system audio capture wording.

If microphone access is missing, grant microphone access to the same launching application.

Re-run `doctor` after changing permissions. It prints the next normal CLI commands when the machine
is usable. Use `murmurmark doctor --strict` when a setup script should fail on missing required
dependencies.

## Short Recording

Run:

```bash
rm -rf ./sessions/smoke
swift run murmurmark record --out ./sessions/smoke --duration 5 --target-bundle system
swift run murmurmark inspect ./sessions/smoke
swift run murmurmark export-audio ./sessions/smoke
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

For real meetings, build once and run the binary directly:

```bash
cd murmurmark
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"
murmurmark doctor
murmurmark record --target-bundle system
```

This is the canonical v1 path for Echo Guard work: ScreenCaptureKit writes separate `audio/mic/000001.caf` and `audio/remote/000001.caf` tracks, and later preprocessing works algorithmically from those two tracks. Do not use BlackHole, Loopback or `--remote-backend audio-input` for normal Echo Guard tests.

Without `--duration`, recording continues until `Ctrl-C`. MurmurMark catches the stop signal, stops capture, closes audio files and writes `session.json`.

Without `--out`, MurmurMark creates a fresh directory under `./sessions`, for example:

```text
recording until Ctrl-C -> ./sessions/<session>
```

Use that printed path for inspection and export:

```bash
SESSION=./sessions/<session>

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

`--mic-backend voice-processing` and `--remote-backend audio-input` are experimental comparison modes. They are not the main product path and should not be used to judge the algorithmic subtraction problem unless the test explicitly says so.

For the normal path, expected `session.json` values are:

```json
{
  "capture_mode": "screencapturekit_system",
  "remote_audio": { "backend": "screencapturekit_audio" },
  "mic_audio": { "backend": "screencapturekit_microphone" }
}
```

If Echo Guard has already run, `derived/asr/mic.wav` is exported from `derived/preprocess/audio/mic_for_asr.wav`. If cleanup was rejected, `mic_for_asr.wav` is the prepared raw mic fallback.

After a transcript exists at `derived/transcript/resolved/transcript.rich.json`, run the transcript-level Echo Guard pass:

```bash
murmurmark reconcile-transcript "$SESSION"
```

It marks mic utterances that look like delayed copies of remote speech and updates `derived/transcript/resolved/quality_report.json`.

If you need a fixed directory for a planned test, pass `--out` explicitly:

```bash
murmurmark record --out ./sessions/my-test --target-bundle system
```

MurmurMark refuses to write into a non-empty output directory.

Echo diagnostics are optional for the minimal capture workflow. They are useful when recording without headphones and you want to know whether remote audio probably leaked into the microphone track.

`local_fir` is the current recommended Echo Guard cleanup engine for real speaker-bleed sessions. It keeps raw CAF files untouched, writes a diagnostic clean mic, writes a role-selected mic for ASR, and keeps raw mic as fallback if the quality gate rejects the candidate. `linear_baseline`, `speexdsp` and `webrtc-apm` remain comparison engines. `speexdsp` requires Homebrew packages `pkgconf` and `speexdsp` when the helper has not been built yet. `webrtc-apm` requires `rust`, `meson`, `ninja`, `cmake` and `abseil` when the helper has not been built yet.

## Known Limitation

The first CLI build uses ScreenCaptureKit as a bridge backend. It proves the session package and export path. The target backend remains Core Audio Process Tap, as described in ADR-0001 and ADR-0008.
