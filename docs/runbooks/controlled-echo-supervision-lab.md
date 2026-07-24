# Controlled Echo Supervision Lab v1

This runbook collects private speaker-mode evidence for a future echo model. It does not train a
model and does not change the production Echo Guard.

Each capture uses the ordinary durable raw writer and stops automatically after about 7 minutes
40 seconds. Run it from a normal macOS Terminal session, not from a background agent.

## One-Time Preparation

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

scripts/install-local.sh
murmurmark doctor --strict
murmurmark echo-lab prepare
```

Before recording, `doctor --strict` must report:

```text
[ok] screen/system audio permission: ok
shareable displays: 1
[ok] microphone permission: ok
```

The exact display count may differ, but it must be greater than zero.

## Acoustic Rules

- Select the built-in Mac speakers as the output device.
- Use the same microphone and placement as in ordinary meetings without headphones.
- Close or mute every application that may play audio.
- Set the requested macOS output volume before each capture and do not change it during capture.
- Stay silent during `silence`, `remote_only` and guard phases.
- Speak each phrase printed after `SAY:` once, naturally and without rushing.
- Type normally, but do not speak, during `keyboard_noise`.
- Do not start Live Shadow, a meeting pipeline or a second recorder.

An excluded capture remains useful diagnostic evidence, but it does not satisfy corpus coverage.
Never edit its raw CAF or weaken a frozen threshold to make it pass.

## Capture Scenarios

Run one scenario at a time. Start with `speaker_train_quiet`; inspect it before spending time on the
other five.

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

murmurmark doctor --strict
murmurmark echo-lab prepare
```

### 1. Train Quiet

Set output volume to `20..35%` and use the normal work position.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-train-quiet"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_train_quiet

murmurmark echo-lab inspect "$SESSION"
```

### 2. Train Normal A

Set output volume to `40..55%` and use the normal work position.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-train-normal-a"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_train_normal_a

murmurmark echo-lab inspect "$SESSION"
```

### 3. Train Normal B

Keep output volume at `40..55%`, but use a deliberately offset or farther normal work position.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-train-normal-b"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_train_normal_b

murmurmark echo-lab inspect "$SESSION"
```

### 4. Train Loud

Set output volume to `60..75%` and return to the normal work position.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-train-loud"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_train_loud

murmurmark echo-lab inspect "$SESSION"
```

### 5. Dev Normal

Run this on a separate day or in a materially different room state. Set output volume to `40..55%`.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-dev-normal"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_dev_normal

murmurmark echo-lab inspect "$SESSION"
```

### 6. Controlled Hard Test

Set output volume to `50..65%` and use the normal work position.

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-hard-doubletalk"
echo "SESSION=\"$SESSION\""

murmurmark echo-lab capture \
  --out "$SESSION" \
  --scenario speaker_hard_doubletalk

murmurmark echo-lab inspect "$SESSION"
```

## Inspect A Capture

`inspect` is safe to repeat. It runs local faster-whisper and Target-Me checks, so it may take
several minutes.

```bash
jq '{
  scenario,
  outcome,
  reasons,
  track_duration_sec,
  required_validators,
  phases: [.phases[] | {phase_id, outcome, reasons}]
}' "$SESSION/derived/echo-lab/inspection.json"
```

If the outcome is `excluded`, fix only the named environmental or capture problem and record a new
session. Do not reuse the same session path.

## Build The Frozen Corpus

After all six captures have passed:

```bash
murmurmark corpus echo-supervision build
murmurmark corpus echo-supervision replay
murmurmark corpus echo-supervision status
```

Private outputs are written to:

```text
sessions/_reports/controlled-echo-supervision-v1/
```

`READY_FOR_ADAPTATION` permits a separate future training goal. `DO_NOT_TRAIN` is the correct result
when coverage, contamination, privacy, split isolation, reconstruction, immutability or replay
gates do not pass. `status` also lists the missing scenarios and prints the next capture command.

## Recovery

If capture is interrupted, retain the partial session for diagnosis and create a new session.
Never resume into its raw files.

These commands are safe to repeat:

```bash
murmurmark echo-lab inspect "$SESSION"
murmurmark corpus echo-supervision build
murmurmark corpus echo-supervision replay
murmurmark corpus echo-supervision status
```
