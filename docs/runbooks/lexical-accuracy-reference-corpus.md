# Lexical Accuracy Reference Corpus v1 Runbook

This is a development corpus command. It is not part of an ordinary meeting lifecycle.

## Import Diagnostic References

Import a timestamped external transcript into ignored private storage:

```bash
murmurmark corpus lexical import SESSION /path/to/reference.txt \
  --source-id machine_reference_1x1_v1 \
  --meeting-mode 1x1 \
  --acoustic-mode speaker_playback \
  --trust-grade independent_machine \
  --local-speaker "Local Speaker Label"
```

Supported formats are timestamp-tab rows and timestamp-range blocks. Repeat `--local-speaker` when
several people share the local microphone. Do not label a machine transcript `human_reviewed`.

## Build And Replay

```bash
murmurmark corpus lexical build \
  --write-manifest docs/testing/lexical-accuracy-reference-corpus-v1-manifest.json

murmurmark corpus lexical status

murmurmark corpus lexical replay \
  --write-manifest docs/testing/lexical-accuracy-reference-corpus-v1-manifest.json
```

Inspect the result:

```bash
REPORT="sessions/_reports/lexical-accuracy-reference-corpus-v1"
cat "$REPORT/lexical_accuracy_reference_report.md"
jq '{decision,summary,gates,evidence_limit,next_goal}' \
  "$REPORT/lexical_accuracy_reference_report.json"
```

The frozen v1 result is:

```text
decision: REFERENCE_INSUFFICIENT
sources: 9
exact generated subset: 67 words, WER 0, CER 0
human-reviewed real sessions: 0
```

Six Echo Lab prompt rows and two external machine transcripts remain diagnostic only. Their WER is
a disagreement measure and must not be quoted as MurmurMark accuracy.

## Add A Human-Reviewed Source

A future source may use `human_reviewed` only after its complete shared interval has been checked
against audio. The first promotable seed must cover at least two sessions and jointly include:

- 1x1 and group meetings;
- `Me` and remote words;
- speaker playback and headphones/low-leak modes.

Private text remains in `sessions/`. After adding evidence, rebuild, replay and rerun:

```bash
murmurmark corpus perfection all
murmurmark corpus perfection all --verify-existing
```

Never update a tracked hash merely to silence a mismatch. Requalify the source and its trust grade
first.
