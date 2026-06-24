# Echo Guard Local FIR Validation

Session:

```text
private validation session, not included in the repository
```

Context:

- normal ScreenCaptureKit capture;
- delay lab found a stable offset around `-76.3 ms`;
- global static FIR did not pass the held-out `6 dB` acceptance gate;
- one manually checked short clip had a remote-dominant fit region first, then local speech.

Experiment:

```bash
START=<seconds>

.venv/bin/python scripts/echo-guard-local-subtract-lab.py "$SESSION" \
  --start-sec "$START" \
  --duration-sec 8 \
  --fit-sec 2 \
  --tail-ms 80 \
  --regularization 1e-2 \
  --strength 1.0
```

Human listening result:

```text
The local FIR candidate with a 2 second fit window and 80 ms tail was the cleanest tested variant.
```

Re-run with `scripts/echo-guard-local-subtract-lab.py`:

```text
mic_after.wav sounded good on this fragment.
```

Interpretation:

Local FIR is a promising direction when the filter is fitted on nearby remote-dominant audio. The global static FIR should not be used as the final cleanup path. The next candidate should be a segment-local or block-adaptive filter that updates on remote-only windows and freezes or weakens subtraction during local speech and double-talk.

Session-wide implementation result:

```text
engine: local_fir
accepted_for_asr: true
median_delay_ms: -76.312
reliable_delay_windows: 440
remote_only_windows: 429
remote_only_level_segments: 374
role_policy: preserve_local
estimated_echo_reduction_db: 16.968
remote_similarity_before: 0.12599
remote_similarity_after: 0.01045
local_only_energy_delta_db_median: 0.0
local_only_vad_duration_ratio: 1.0
role_masked_silence_sec: 46.048
role_preview_sec: 2383.6
```

ASR role-mask update:

```text
mic_clean_local_fir.wav remains the listenable diagnostic cleanup.
mic_role_masked_for_asr.wav is the selected ASR mic when the quality gate accepts.
mic_for_asr.wav is copied from mic_role_masked_for_asr.wav when the local_fir quality gate accepts.
```

Default role policy:

```text
preserve_local
```

This policy keeps ambiguous remote-active chunks as mildly cleaned/flagged audio instead of hard-muting them. It is intentionally biased toward preserving local speech. The stricter `role_safe` and `strict_silence` policies remain available for comparison, but they are not the default after real-session listening showed that aggressive masking can remove greetings and quiet local replies.

Manual regression checks after the preserve-local threshold update:

```text
the highest-risk muted chunks were checked by ear and matched the intended role policy
```

Interpretation:

- the original FIR subtraction direction is valid;
- a full deletion of remote sound from mic is not reliable enough as the default;
- current v1 behavior should optimize for ASR usefulness and role safety, not studio-quality mic cleanup;
- future work should evaluate ASR on `mic_role_masked_for_asr.wav` and compare it with raw mic plus transcript-level reconciliation.
