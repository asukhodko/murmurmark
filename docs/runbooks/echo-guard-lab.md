# Echo Guard Delay Lab

Use this runbook before trying another cleanup engine on a real meeting. The goal is to measure whether the `remote` reference can explain the leaked remote sound inside `mic`.

This is investigation only. It must not modify raw audio and it must not decide that a clean mic is safe for ASR.

## Input

Start from a normal ScreenCaptureKit session:

```bash
SESSION=./sessions/<session>

.build/debug/murmurmark inspect "$SESSION"
.build/debug/murmurmark preprocess "$SESSION" --echo diagnostic
```

The session should show:

```text
capture_mode: screencapturekit_system
remote_audio.backend: screencapturekit_audio
mic_audio.backend: screencapturekit_microphone
```

Do not use `--remote-backend audio-input`, BlackHole or Loopback for this lab.

## Delay Lab

Run:

```bash
.venv/bin/python scripts/echo-guard-delay-lab.py "$SESSION"
```

The script reads:

```text
derived/preprocess/audio/remote_for_aec.wav
derived/preprocess/audio/mic_raw_for_asr.wav
```

It writes:

```text
derived/preprocess/echo/lab/delay_map.json
derived/preprocess/echo/lab/delay_windows.jsonl
derived/preprocess/echo/lab/candidate_clips.jsonl
```

Inspect the summary:

```bash
jq '.summary' "$SESSION/derived/preprocess/echo/lab/delay_map.json"
```

Delay sign convention:

```text
positive delay_ms: mic lags remote in the decoded files
negative delay_ms: mic leads remote in the decoded files
```

A negative delay is possible in offline files because ScreenCaptureKit system audio and microphone capture can have different buffering and timestamp alignment. It is a file-alignment fact, not an acoustic claim.

## What Good Looks Like

The delay estimator is good enough to continue only if:

- reliable windows exist on remote-heavy parts of the call;
- delay does not jump randomly across the whole search range;
- the p10..p90 delay range is narrow enough to describe one echo path or a smooth drift;
- top candidate clips are actually remote-only or remote-dominant when checked by ear.

If the report still says something like `median_delay_ms: 0` with a huge delay range, do not run another cleanup engine yet. Fix reference selection, delay estimation or clip selection first.

## Candidate Clip Review

List the top candidates:

```bash
jq -r '[.start_sec, .end_sec, .delay_ms, .confidence, .remote_db, .mic_db] | @tsv' \
  "$SESSION/derived/preprocess/echo/lab/candidate_clips.jsonl" | head -20
```

Extract one candidate for listening:

```bash
START=123.45
DUR=10

ffmpeg -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/mic_raw_for_asr.wav" \
  "$SESSION/derived/preprocess/echo/lab/candidate_mic.wav"

ffmpeg -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/remote_for_aec.wav" \
  "$SESSION/derived/preprocess/echo/lab/candidate_remote.wav"

afplay "$SESSION/derived/preprocess/echo/lab/candidate_mic.wav"
afplay "$SESSION/derived/preprocess/echo/lab/candidate_remote.wav"
```

Mark clips manually as:

```text
remote_only
local_only
double_talk
silence
path_change
```

The next cleanup prototype should fit only on `remote_only` clips and should freeze or become very conservative on `double_talk`.

## Static FIR Sanity Check

After at least one strong remote-only candidate is confirmed by ear, run the static FIR check:

```bash
.venv/bin/python scripts/echo-guard-fir-lab.py "$SESSION" \
  --lab-dir "$SESSION/derived/preprocess/echo/lab_margin3" \
  --fit-clips 5 \
  --eval-clips 5 \
  --split alternating \
  --out-dir "$SESSION/derived/preprocess/echo/fir_lab_alt"
```

Inspect:

```bash
jq '.summary, .eval_metrics' "$SESSION/derived/preprocess/echo/fir_lab_alt/fir_report.json"
```

The script writes short listening examples:

```text
derived/preprocess/echo/fir_lab_alt/examples/*_remote.wav
derived/preprocess/echo/fir_lab_alt/examples/*_mic_before.wav
derived/preprocess/echo/fir_lab_alt/examples/*_echo_hat.wav
derived/preprocess/echo/fir_lab_alt/examples/*_mic_after.wav
```

Listen to at least one held-out example:

```bash
afplay "$SESSION/derived/preprocess/echo/fir_lab_alt/examples/eval_01_330.0s_mic_before.wav"
afplay "$SESSION/derived/preprocess/echo/fir_lab_alt/examples/eval_01_330.0s_mic_after.wav"
afplay "$SESSION/derived/preprocess/echo/fir_lab_alt/examples/eval_01_330.0s_echo_hat.wav"
```

The FIR sanity check is good enough to continue only if held-out median reduction is at least about `6 dB` and the held-out clips sound better. If fit clips improve but held-out clips do not, the static echo path does not generalize and the next algorithm should be local/adaptive.

## Local Subtraction Clip Check

When a manually checked clip has remote audio first and local speech later, run a local FIR check:

```bash
START=<seconds>
OUT_DIR="$SESSION/derived/preprocess/echo/local_subtract_${START}_script"

.venv/bin/python scripts/echo-guard-local-subtract-lab.py "$SESSION" \
  --start-sec "$START" \
  --duration-sec 8 \
  --fit-sec 2 \
  --tail-ms 80 \
  --regularization 1e-2 \
  --strength 1.0 \
  --out-dir "$OUT_DIR"
```

Listen:

```bash
DIR="$OUT_DIR"

afplay "$DIR/mic_before.wav"
afplay "$DIR/mic_after.wav"
afplay "$DIR/echo_hat.wav"
```

This check is useful only when the fit window is remote-dominant and the later part contains local speech. If `mic_after.wav` keeps local speech intact while reducing the first remote leak, the next production candidate should be a segment-local or block-adaptive filter, not one static filter for the whole meeting.

## Session-Wide Local FIR Cleanup

Run the current recommended whole-session cleaner:

```bash
.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
.build/debug/murmurmark inspect "$SESSION" --echo
```

The default policy is `preserve_local`: ambiguous remote-active regions are preserved as mildly cleaned audio and flagged in `speaker_state.jsonl`. For stricter role protection, use:

```bash
.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir --echo-policy role_safe
```

For maximum silence of remote-active mic regions, use `--echo-policy strict_silence`; this can delete local overlap speech.

Expected outputs:

```text
derived/preprocess/audio/mic_clean_local_fir.wav
derived/preprocess/audio/mic_role_masked_for_asr.wav
derived/preprocess/audio/mic_role_preview.wav
derived/preprocess/audio/echo_hat_local_fir.wav
derived/preprocess/mic_asr_segments/segments_manifest.json
derived/preprocess/echo/local_fir_report.json
derived/preprocess/echo/local_fir_segments.jsonl
derived/preprocess/echo/speaker_state.jsonl
derived/preprocess/echo/echo_suppression_report.json
```

If the quality gate accepts the candidate, `mic_for_asr.wav` is copied from `mic_role_masked_for_asr.wav`. `mic_clean_local_fir.wav` remains available for listening and diagnostics. If the gate rejects the candidate, both derived files are still available, but `mic_for_asr.wav` remains the raw mic fallback.

For listening checks, prefer `mic_role_preview.wav`; it concatenates merged retained local/overlap/uncertain chunks with small guard margins, without the long remote-only silences preserved in the full-timeline `mic_role_masked_for_asr.wav`.

Listen to the full selected mic when you want timeline fidelity:

```bash
afplay "$SESSION/derived/preprocess/audio/mic_role_masked_for_asr.wav"
```

Listen to the preview when you want a fast check for dropped local speech:

```bash
afplay "$SESSION/derived/preprocess/audio/mic_role_preview.wav"
```

Check the report:

```bash
jq '.summary, .metrics, .decision' "$SESSION/derived/preprocess/echo/local_fir_report.json"
```

If local speech seems to disappear, inspect the muted chunks:

```bash
jq -r 'select(.action == "mute_silence") | [.start, .end, .mic_db, .remote_db] | @tsv' \
  "$SESSION/derived/preprocess/echo/speaker_state.jsonl" | sort -k3,3nr | head -20
```

Extract the most suspicious chunks before changing thresholds:

```bash
START=6
DUR=2
OUT="$SESSION/derived/preprocess/echo/role_drop_audit"
mkdir -p "$OUT"

ffmpeg -hide_banner -loglevel error -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/mic_raw_for_asr.wav" \
  -ar 16000 -ac 1 "$OUT/${START}_raw_mic.wav"

ffmpeg -hide_banner -loglevel error -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/mic_role_masked_for_asr.wav" \
  -ar 16000 -ac 1 "$OUT/${START}_role_masked.wav"

ffmpeg -hide_banner -loglevel error -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/remote_for_aec.wav" \
  -ar 16000 -ac 1 "$OUT/${START}_remote.wav"

afplay "$OUT/${START}_raw_mic.wav"
afplay "$OUT/${START}_remote.wav"
afplay "$OUT/${START}_role_masked.wav"
```

For a manually validated fragment, extract the cleaned result from the whole-session candidate:

```bash
START=<seconds>
DUR=8

ffmpeg -y -ss "$START" -t "$DUR" \
  -i "$SESSION/derived/preprocess/audio/mic_clean_local_fir.wav" \
  "$SESSION/derived/preprocess/echo/local_subtract_${START}_from_session.wav"

afplay "$SESSION/derived/preprocess/echo/local_subtract_${START}_from_session.wav"
```

## Stop Rules

Stop audio cleanup work and prefer transcript-level suppression when:

- delay confidence is unstable on remote-only clips;
- a static FIR model cannot reduce held-out remote-only leakage by at least 6 dB;
- cleanup damages local-only speech;
- double-talk becomes worse by ear or by ASR;
- ASR on clean mic is not better than ASR on raw mic.

The product goal is not studio-quality microphone audio. The product goal is to avoid treating leaked remote speech as the user's speech.
