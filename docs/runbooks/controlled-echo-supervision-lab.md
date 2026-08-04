# Controlled Echo Supervision Lab v1

This runbook records how the frozen private speaker-mode evidence was collected. It does not train
a model and does not change the production Echo Guard.

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
  The capture now checks it throughout the run and aborts as soon as drift exceeds the frozen limit.
- Stay silent during `silence`, `remote_only` and guard phases.
- Speak each phrase printed after `>>> ПРОИЗНЕСИ ВСЛУХ СЕЙЧАС:` once, naturally and without
  rushing. These phrases are instructions for you, not text that macOS will play.
- Type normally, but do not speak, during `keyboard_noise`. Type in another application such as
  Notes, not in the terminal running MurmurMark: terminal input would remain buffered and could be
  executed by the shell after capture exits.
- Do not start Live Shadow, a meeting pipeline or a second recorder.

The synthetic voice is deliberately used only for the remote participant. Local prompts must be
spoken in your real voice because they measure Target-Me preservation. Before raw capture starts,
the command prints this distinction and requires the exact confirmation `ГОТОВ`. It then prints a
large Russian instruction at every phase boundary.

For deliberate non-interactive automation, read the same instructions first and pass
`--confirm-operator-instructions`. The flag acknowledges the instructions; it does not synthesize
the local phrases.

An excluded capture remains useful diagnostic evidence, but it does not satisfy corpus coverage.
Never edit its raw CAF or weaken a frozen threshold to make it pass.

If capture aborts, `derived/echo-lab/capture_abort.json` records the phase and reason. Run
`murmurmark echo-lab inspect "$SESSION"` to see the same actionable failure, then use a fresh
`SESSION` for the retry.

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

Inspection selects the first input channel when creating its mono analysis track. The generated
lab stimulus is duplicated across the system-capture stereo channels; an ordinary normalized
stereo downmix would add about 3 dB and falsely report clipping. Short opening phrases use a wider
speaker-validation window than double-talk, while keeping the same frozen Target-Me threshold.
Their local-level gate also applies the unchanged `local_speech_min_rms_db` threshold to the union
of ASR-confirmed word intervals, not to scheduled pauses across the whole phase. Missing intervals
therefore remain `local_speech_too_quiet`; pauses cannot dilute an otherwise confirmed short phrase.
If you react late but still speak inside the requested phase, the capture may remain valid; empty
four-second local, double-talk and opening windows are excluded from corpus coverage instead of
being mislabeled as spoken items.

Raw echo is not a valid Target-Me oracle by itself: room and speaker coloration can make a remote
voice embedding resemble the enrolled local speaker. `inspect` therefore creates a temporary
`local_fir` clean candidate from the same immutable analysis inputs. Remote-only contamination is
decided from the clean residual paired with the exact source chunk; raw scores remain in the report
for diagnosis. Missing clean evidence fails closed.

The same clean path transcribes bounded double-talk. Inspection records raw and cleaned prompt
recall separately and selects the stronger source under the unchanged frozen recall threshold.
Corpus support is narrower still: only discriminative words from an expected local prompt, absent
from the remote stimulus vocabulary, may support a four-second double-talk item.

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

The frozen v1 result is `READY_FOR_ADAPTATION`. Five train, one dev and one hard-test capture passed
all gates. Train contains `620s` local-only, `640s` remote-only and `1804s` synthetic mixtures; dev
contains `124s`, `128s` and `352s`; hard-test contains `68s` measured double-talk. Replay matches
`1465/1465`. Do not collect more v1 captures. The lab first supplied Speaker-Preserving Neural Echo
v2, which later completed with guarded PROMOTE. Reference-Conditioned Target-Me Separation v1 then
used the same immutable ownership and completed with `DO_NOT_PROMOTE`: the corpus lacked labelled
non-target local speech. Do not relabel or extend frozen v1. Build the separate Target-Me
Identifiability Corpus v1 for correct/wrong enrollment and other-speaker evidence.

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
