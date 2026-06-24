# 2026-06-22 Talk Validation

Manual validation on macOS 26.5.1.

## Environment

- Command: `swift run murmurmark`
- Backend: `screencapturekit_system`
- Permissions:
  - Screen/system audio: `ok`
  - Microphone: `ok`
- Output session path: `./sessions/talk-solo`
- Export path: `./sessions/talk-solo/derived/asr`

## Solo Talk Check

Command:

```bash
swift run murmurmark record \
  --out ./sessions/talk-solo \
  --duration 60 \
  --target-bundle system
```

Result:

- `inspect` reported `health: ok`.
- `audio/mic/000001.caf` contained the local microphone voice.
- `audio/remote/000001.caf` was silent, as expected without other participants or remote playback.
- `export-audio` produced `mic.wav` and `remote.wav`.
- Both exported WAV files were `16000 Hz`, mono, about 63.8 seconds long.

## Talk Channel With Other Participants

Command:

```bash
swift run murmurmark record \
  --out ./sessions/talk-solo \
  --duration 60 \
  --target-bundle system
```

Result:

- `inspect` reported `health: ok`.
- `audio/mic/000001.caf` contained the local microphone voice.
- The mic track also contained some remote audio bleed because speakers were used without headphones.
- `audio/remote/000001.caf` contained remote participant audio clearly.
- `export-audio` produced `mic.wav` and `remote.wav`.
- Exported WAV parameters:
  - `mic.wav`: `16000 Hz`, mono, `61.728000` seconds.
  - `remote.wav`: `16000 Hz`, mono, `61.780000` seconds.

## Conclusion

The first minimal version records both local microphone and remote/system audio, writes a valid session package, and prepares ASR-ready WAV files. For cleaner source separation during manual tests, use headphones so remote audio does not leak back into the microphone track.
