# Controlled Remote Speaker Truth Lab v1 Runbook

Updated: 2026-08-08

## Build And Evaluate

```bash
murmurmark corpus remote-truth-lab build
murmurmark corpus remote-truth-lab evaluate
murmurmark corpus remote-truth-lab status
```

The build is fully local. It uses pinned macOS `say` voices and writes generated speech only below
`sessions/_reports/controlled-remote-speaker-truth-lab-v1/private/`.

## Replay

```bash
murmurmark corpus remote-truth-lab replay
```

Replay verifies every frozen input hash, recomputes the public aggregate report from private truth
and predictions, and fails when any byte or decision changes.

## Interpretation

- `LAB_READY`: the WavLM candidate passed synthetic held-out gates. Real transcripts remain unchanged.
- `DO_NOT_ADVANCE`: at least one WavLM candidate gate failed; inspect the separate control/candidate
  decisions and private prediction audit. A qualified Coverage v3 control does not override this.
- `BLOCKED`: a local renderer, voice, model, runtime or frozen artifact is unavailable or stale.

The report is:

```text
sessions/_reports/controlled-remote-speaker-truth-lab-v1/controlled_remote_speaker_truth_lab_report.json
```

Do not copy generated speech or exact scripts into tracked documentation. Do not use hard-split
results to retune thresholds.
